"""
MongoDB service for storing STT transcripts:
- Rooms collection: Information about rooms and processing status
- Tracks collection: Metadata of audio tracks (reference to rooms)
- Transcript_chunks collection: Segments divided into chunks (max 200 items/chunk)
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
from bson import ObjectId
import logging
import asyncio
import aiohttp
from stt_service.config.app_config import get_config
from pymongo import ReturnDocument

logger = logging.getLogger(__name__)


class MongoDBService:
    """Service for storing track-based transcripts in MongoDB"""

    _instance = None
    CHUNK_SIZE = 50  # Maximum segments per chunk

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.config = get_config()
        
        # Build MongoDB URI with authentication
        self.mongo_uri = self._build_mongo_uri()
        self.database_name = self.config.mongodb.database
        self.CHUNK_SIZE = self.config.mongodb.chunk_size

        # Collection names
        self.rooms_collection_name = "rooms"
        self.tracks_collection_name = "tracks"
        self.chunks_collection_name = "transcript_chunks"

        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.rooms_collection = None
        self.tracks_collection = None
        self.chunks_collection = None
        self.connected = False

        self._initialized = True
        logger.info(
            f"MongoDBService initialized (DB={self.database_name}, "
            f"Collections={self.rooms_collection_name}, {self.tracks_collection_name}, {self.chunks_collection_name})"
        )

    def _build_mongo_uri(self) -> str:
        """Build MongoDB connection URI with authentication"""
        mongo_config = self.config.mongodb
        return (
            f"mongodb://{mongo_config.username}:{mongo_config.password}@"
            f"{mongo_config.host}:{mongo_config.port}/?authSource=admin"
        )

    async def connect(self) -> bool:
        """Establish connection to MongoDB"""
        if self.connected:
            return True

        try:
            self.client = AsyncIOMotorClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            
            self.db = self.client[self.database_name]
            self.rooms_collection = self.db[self.rooms_collection_name]
            self.tracks_collection = self.db[self.tracks_collection_name]
            self.chunks_collection = self.db[self.chunks_collection_name]
            
            # Test connection
            await self.client.admin.command("ping")
            await self._create_indexes()

            self.connected = True
            logger.info("✅ Connected to MongoDB with authentication")
            return True

        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            self.connected = False
            return False

    async def _create_indexes(self):
        """Create indexes for all collections"""
        try:
            # Indexes for rooms collection
            await self.rooms_collection.create_index("room_name")
            await self.rooms_collection.create_index("status")
            await self.rooms_collection.create_index("created_at")

            # Indexes for tracks collection
            await self.tracks_collection.create_index("egress_id", unique=True)
            await self.tracks_collection.create_index("track_id")
            await self.tracks_collection.create_index("room_ref_id")
            await self.tracks_collection.create_index("participant_identity")
            await self.tracks_collection.create_index("created_at")

            # Indexes for transcript_chunks collection
            await self.chunks_collection.create_index("track_ref_id")
            await self.chunks_collection.create_index(
                [("track_ref_id", 1), ("chunk_index", 1)],
                unique=True
            )
            await self.chunks_collection.create_index("start_time")
            await self.chunks_collection.create_index("end_time")
            await self.chunks_collection.create_index("item_count")

            logger.info("✅ MongoDB indexes created for all collections")

        except Exception as e:
            logger.warning(f"Failed to create indexes: {e}")

    async def disconnect(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("MongoDB disconnected")

    # ==========================================================
    # 🏠 ROOM METHODS
    # ==========================================================

    async def create_or_get_room(
        self,
        room_name: str,
        initial_track_count: int = 1,
        start_session_time: str = "",
        status: str =  "pending"
    ) -> Optional[ObjectId]:

        if not self.connected and not await self.connect():
            logger.error("Cannot create/get room: MongoDB not connected")
            return None

        try:
            room = await self.rooms_collection.find_one_and_update(
                {
                    "room_name": room_name,
                    "start_session_time": start_session_time
                },
                {
                    "$setOnInsert": {
                        "room_name": room_name,
                        "completed_tracks": 0,
                        "status": status,
                        "start_session_time": start_session_time,
                        "created_at": datetime.strptime(start_session_time, "%Y%m%d_%H%M%S"),
                        "completed_at": None
                    },
                    "$inc": {
                        "remain_tracks": initial_track_count
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER  # ⭐ quan trọng
            )

            return room
        except PyMongoError as e:
            logger.error(f"Failed to create/get room: {e}")
            return None


    async def increment_remain_tracks(
        self,
        room_ref_id: ObjectId,
        count: int = 1
    ) -> bool:
        """
        Increase the number of remain_tracks when a new track is added.
        Args:
            room_ref_id: Room reference ID
            count: Number of tracks to increase (default: 1)
        Returns:
            True if successful
        """
        if not self.connected:
            return False

        try:
            result = await self.rooms_collection.update_one(
                {"_id": room_ref_id},
                {
                    "$inc": {"remain_tracks": count},
                    "$set": {"status": "processing"}  
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"📈 Incremented remain_tracks by {count} for room_id={room_ref_id}")
                return True
            return False

        except PyMongoError as e:
            logger.error(f"Failed to increment remain_tracks: {e}")
            return False

    async def complete_track_in_room(
        self,
        room_ref_id: ObjectId
    ) -> bool:
        """
        Update when 1 track is completed:
        - Decrement remain_tracks
        - Increment completed_tracks
        - Automatically update status if remain_tracks = 0

        Args:
            room_ref_id: Room reference ID

        Returns:
            True if successful
        """
        if not self.connected:
            return False

        try:
            from pymongo import ReturnDocument
            
            # Atomic update: increment completed_tracks and decrement remain_tracks
            room = await self.rooms_collection.find_one_and_update(
                {"_id": room_ref_id},
                {
                    "$inc": {
                        "completed_tracks": 1,
                        "remain_tracks": -1
                    }
                },
                return_document=ReturnDocument.AFTER
            )
            
            if not room:
                logger.error(f"Room not found: room_id={room_ref_id}")
                return False
            
            logger.info(
                f"✅ Track completed: room_id={room_ref_id}, "
                f"remain={room['remain_tracks']}, completed={room['completed_tracks']}"
            )
            
            # Auto-update status when all tracks completed
            if room["remain_tracks"] == 0 and room["status"] == "final_room":
                await self.rooms_collection.update_one(
                    {"_id": room_ref_id},
                    {
                        "$set": {
                            "status": "completed",
                            "completed_at": datetime.utcnow()
                        }
                    }
                )
                logger.info(f"🎉 All tracks completed for room_id={room_ref_id}")
                
                # Trigger summary generation
                asyncio.create_task(self._trigger_summary_api(str(room_ref_id)))
            
            return True

        except PyMongoError as e:
            logger.error(f"Failed to complete track in room: {e}")
            return False

    async def fail_track_in_room(
        self,
        room_ref_id: ObjectId
    ) -> bool:
        """
        Cập nhật khi 1 track thất bại:
        - Giảm remain_tracks
        - Không tăng completed_tracks
        - Update status nếu cần
        
        Args:
            room_ref_id: Room reference ID
            
        Returns:
            True if successful
        """
        if not self.connected:
            return False

        try:
            from pymongo import ReturnDocument
            
            # Atomic update: only decrement remain_tracks
            room = await self.rooms_collection.find_one_and_update(
                {"_id": room_ref_id},
                {
                    "$inc": {"remain_tracks": -1}
                },
                return_document=ReturnDocument.AFTER
            )
            
            if not room:
                logger.error(f"Room not found: room_id={room_ref_id}")
                return False
            
            logger.info(f"⚠️ Track failed: room_id={room_ref_id}, remain={room['remain_tracks']}")
            
            # If no more tracks remaining, mark as completed (with some failures)
            if room["remain_tracks"] == 0:
                await self.rooms_collection.update_one(
                    {"_id": room_ref_id},
                    {
                        "$set": {
                            "status": "completed",
                            "completed_at": datetime.utcnow()
                        }
                    }
                )
                logger.info(f"🏁 All tracks processed (with failures) for room_id={room_ref_id}")
            
            return True

        except PyMongoError as e:
            logger.error(f"Failed to update failed track in room: {e}")
            return False
        
    async def get_room_by_id(self, room_ref_id: ObjectId) -> Optional[Dict]:
        """Get room by ID"""
        if not self.connected:
            return None
        return await self.rooms_collection.find_one({"_id": room_ref_id})

    async def get_room_by_name(self, room_name: str) -> Optional[Dict]:
        """Get room by name"""
        if not self.connected:
            return None
        return await self.rooms_collection.find_one({"room_name": room_name})

    async def get_room_session_by_name(self, room_name: str, start_session_time: str) -> Optional[Dict]:
        """Get room by name"""
        print(f"room_name={room_name}, start_session_time={start_session_time}")
        if not self.connected:
            return None
        return await self.rooms_collection.find_one({"room_name": room_name, "start_session_time": start_session_time.strip()})

    async def update_room_status(
        self,
        room_ref_id: ObjectId,
        status: str
    ) -> bool:
        """Manually update room status"""
        if not self.connected:
            return False

        try:
            update_doc = {"$set": {"status": status}}
            
            if status == "completed":
                update_doc["$set"]["completed_at"] = datetime.utcnow()
            
            result = await self.rooms_collection.update_one(
                {"_id": room_ref_id},
                update_doc
            )
            return result.modified_count > 0

        except PyMongoError as e:
            logger.error(f"Failed to update room status: {e}")
            return False

    async def final_room_status(self, room_name: str, start_session_time: str) -> bool:

        if not self.connected:
            return False

        try:
            room = await self.create_or_get_room(room_name,0, start_session_time ,"final_room")
            logger.info(f"Finalizing room: {room_name},{start_session_time} with _id={room["_id"]}")
            # only update when status not finalized
            if room["status"] in ["final_room", "completed"]:
                logger.warning(f"Room already finalized: {room_name}")
                return True
            new_status = "final_room" if room["remain_tracks"] > 0 else "completed"
            
            update_doc = {
                "$set": {
                    "status": new_status,
                    "finalized_at": datetime.utcnow()
                }
            }
            
            await self.rooms_collection.update_one(
                {"_id": room["_id"]},
                update_doc
            )
            
            logger.info(f"🔒 Room finalized: {room_name} → {new_status}")
            if new_status == "completed":
                asyncio.create_task(self._trigger_summary_api(str(room["_id"])))
            return True

        except PyMongoError as e:
            logger.error(f"Failed to finalize room: {e}")
            return False

    # ==========================================================
    # 🔥 TRACK METHODS (Updated to use room_ref_id)
    # ==========================================================

    async def save_track_metadata(
        self,
        *,
        egress_id: str,
        track_id: str,
        room_ref_id: ObjectId,
        participant_identity: str,
        audio_info: Dict[str, Any],
        status: str = "processing",
    ) -> Optional[ObjectId]:
        """
        Save track metadata with room reference
        
        Args:
            egress_id: Unique egress identifier
            track_id: Track identifier
            room_ref_id: Reference to room document _id
            participant_identity: Participant identity
            audio_info: Dict containing {filename, duration_sec, started_at_ns, ended_at_ns}
            status: Track status (default: "processing")
            
        Returns:
            ObjectId of inserted document, or None if failed
        """
        if not self.connected and not await self.connect():
            logger.error("Cannot save track metadata: MongoDB not connected")
            return None

        # Use find_one_and_update with upsert for atomic operation
        # This avoids race conditions and is more efficient than insert-then-catch
        try:
            from pymongo import ReturnDocument
            
            result = await self.tracks_collection.find_one_and_update(
                {"egress_id": egress_id},
                {
                    "$setOnInsert": {
                        "egress_id": egress_id,
                        "track_id": track_id,
                        "room_ref_id": room_ref_id,
                        "participant_identity": participant_identity,
                        "audio_info": {
                            "filename": audio_info.get("filename"),
                            "duration_sec": audio_info.get("duration_sec"),
                            "started_at_ns": audio_info.get("started_at_ns"),
                            "ended_at_ns": audio_info.get("ended_at_ns"),
                        },
                        "chunk_count": 0,
                        "status": status,
                        "created_at": datetime.utcnow(),
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER
            )
            
            if result:
                logger.info(
                    f"📝 Track metadata saved: egress={egress_id}, "
                    f"track={track_id}, room_ref={room_ref_id}, _id={result['_id']}"
                )
                return result["_id"]
            else:
                logger.warning(f"Track metadata operation returned None: egress={egress_id}")
                return None

        except PyMongoError as e:
            logger.error(f"Failed to save track metadata: {e}")
            return None

    # ==========================================================
    # 🔥 TRANSCRIPT CHUNKS METHODS (Unchanged)
    # ==========================================================

    def _split_into_chunks(self, segments: List[Dict]) -> List[List[Dict]]:
        """Split segments into chunks of CHUNK_SIZE"""
        chunks = []
        for i in range(0, len(segments), self.CHUNK_SIZE):
            chunk = segments[i:i + self.CHUNK_SIZE]
            chunks.append(chunk)
        return chunks

    def _create_chunk_document(
        self,
        track_ref_id: ObjectId,
        chunk_index: int,
        segments: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Create a chunk document from segments"""
        if not segments:
            raise ValueError("Cannot create chunk document with empty segments")

        return {
            "track_ref_id": track_ref_id,
            "chunk_index": chunk_index,
            "start_time": segments[0].get("start", 0),
            "end_time": segments[-1].get("end", 0),
            "item_count": len(segments),
            "segments": segments,
        }

    # ==========================================================
    # 🔄 UPDATE METHODS
    # ==========================================================

    async def update_track_status(
        self,
        track_ref_id: ObjectId,
        room_ref_id: ObjectId,
        status: str
    ) -> bool:
        """
        Update track status and automatically update room counters.
        
        Args:
            track_ref_id: Track reference ID
            room_ref_id: Room reference ID
            status: New status ('processing' | 'completed' | 'failed')
            
        Returns:
            True if successful
        """
        if not self.connected:
            return False

        try:
            # Get current track status to check if this is a status change
            current_track = await self.tracks_collection.find_one({"_id": track_ref_id})
            
            if not current_track:
                logger.error(f"Track not found: track_id={track_ref_id}")
                return False
            
            old_status = current_track.get("status", "")
            
            # Update track status
            result = await self.tracks_collection.update_one(
                {"_id": track_ref_id},
                {"$set": {"status": status, "updated_at": datetime.utcnow()}}
            )
            
            if result.modified_count == 0:
                logger.warning(f"Track status not modified: track_id={track_ref_id}")
                return False
            
            logger.info(f"📝 Track status updated: {old_status} → {status} (track_id={track_ref_id})")
            
            # Update room counters based on new status
            # Only update room if transitioning to a final state
            if status == "completed" and old_status != "completed":
                await self.complete_track_in_room(room_ref_id)
            elif status == "failed" and old_status not in ["completed", "failed"]:
                await self.fail_track_in_room(room_ref_id)
            
            return True

        except PyMongoError as e:
            logger.error(f"Failed to update track status: {e}")
            return False
        
    async def append_transcript_chunk(
        self,
        track_ref_id: ObjectId,
        new_segments: List[Dict[str, Any]]
    ) -> bool:
        """Append new segments as additional chunks"""
        if not new_segments:
            return True

        last_chunk = await self.chunks_collection.find_one(
            {"track_ref_id": track_ref_id},
            sort=[("chunk_index", -1)]
        )

        start_index = (last_chunk["chunk_index"] + 1) if last_chunk else 0
        chunks = self._split_into_chunks(new_segments)
        chunk_documents = []

        for i, chunk_segments in enumerate(chunks):
            chunk_doc = self._create_chunk_document(
                track_ref_id=track_ref_id,
                chunk_index=start_index + i,
                segments=chunk_segments
            )
            chunk_documents.append(chunk_doc)

        try:
            if chunk_documents:
                await self.chunks_collection.insert_many(chunk_documents)
                
                # Update chunk_count in tracks collection
                # Increment by the actual number of chunks inserted
                await self.tracks_collection.update_one(
                    {"_id": track_ref_id},
                    {
                        "$inc": {"chunk_count": len(chunk_documents)}
                    }
                )

                logger.info(
                    f"✅ Appended {len(chunk_documents)} chunks "
                    f"for track_ref_id={track_ref_id}"
                )
            return True

        except PyMongoError as e:
            logger.error(f"Failed to append chunks: {e}")
            return False

    # ==========================================================
    # 🔍 QUERY METHODS - TRACKS
    # ==========================================================

    async def get_track_by_id(self, track_ref_id: ObjectId) -> Optional[Dict]:
        """Get track metadata by _id"""
        if not self.connected:
            return None
        return await self.tracks_collection.find_one({"_id": track_ref_id})

    async def get_track_by_egress_id(self, egress_id: str) -> Optional[Dict]:
        """Get track metadata by egress_id"""
        if not self.connected:
            return None
        return await self.tracks_collection.find_one({"egress_id": egress_id})

    async def get_room_tracks(self, room_ref_id: ObjectId) -> List[Dict]:
        """Get all tracks for a room"""
        if not self.connected:
            return []
        cursor = self.tracks_collection.find(
            {"room_ref_id": room_ref_id}
        ).sort("created_at", 1)
        return await cursor.to_list(None)

    async def get_room_tracks_by_name(self, room_name: str) -> List[Dict]:
        """Get all tracks for a room by room name"""
        room = await self.get_room_by_name(room_name)
        if not room:
            return []
        return await self.get_room_tracks(room["_id"])

    # ==========================================================
    # 🔍 QUERY METHODS - CHUNKS (Unchanged)
    # ==========================================================

    async def get_track_chunks(
        self,
        track_ref_id: ObjectId,
        chunk_index: Optional[int] = None
    ) -> List[Dict]:
        """Get transcript chunks for a track"""
        if not self.connected:
            return []

        query = {"track_ref_id": track_ref_id}
        if chunk_index is not None:
            query["chunk_index"] = chunk_index

        cursor = self.chunks_collection.find(query).sort("chunk_index", 1)
        return await cursor.to_list(None)

    async def get_chunks_by_time_range(
        self,
        track_ref_id: ObjectId,
        start_time: float,
        end_time: float
    ) -> List[Dict]:
        """Get chunks that overlap with given time range"""
        if not self.connected:
            return []

        query = {
            "track_ref_id": track_ref_id,
            "$or": [
                {"start_time": {"$lte": end_time}, "end_time": {"$gte": start_time}}
            ]
        }

        cursor = self.chunks_collection.find(query).sort("chunk_index", 1)
        return await cursor.to_list(None)

    async def get_full_transcript(self, track_ref_id: ObjectId) -> List[Dict]:
        """Get complete transcript by combining all chunks"""
        chunks = await self.get_track_chunks(track_ref_id)
        
        all_segments = []
        for chunk in chunks:
            all_segments.extend(chunk.get("segments", []))
        
        return all_segments

    # ==========================================================
    # 📊 STATS & UTILITIES
    # ==========================================================

    async def get_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        if not self.connected:
            return {}

        try:
            total_rooms = await self.rooms_collection.count_documents({})
            total_tracks = await self.tracks_collection.count_documents({})
            total_chunks = await self.chunks_collection.count_documents({})

            # Room statistics
            room_stats = await self.rooms_collection.aggregate([
                {"$group": {
                    "_id": "$status",
                    "count": {"$sum": 1}
                }}
            ]).to_list(None)

            # Chunk statistics
            chunk_stats = await self.chunks_collection.aggregate([
                {"$group": {
                    "_id": None,
                    "avg_item_count": {"$avg": "$item_count"},
                    "total_segments": {"$sum": "$item_count"}
                }}
            ]).to_list(1)
            
            return {
                "database": self.database_name,
                "collections": {
                    "rooms": self.rooms_collection_name,
                    "tracks": self.tracks_collection_name,
                    "chunks": self.chunks_collection_name,
                },
                "counts": {
                    "total_rooms": total_rooms,
                    "total_tracks": total_tracks,
                    "total_chunks": total_chunks,
                    "total_segments": chunk_stats[0]["total_segments"] if chunk_stats else 0,
                },
                "room_status": {stat["_id"]: stat["count"] for stat in room_stats},
                "averages": {
                    "segments_per_chunk": chunk_stats[0]["avg_item_count"] if chunk_stats else 0,
                }
            }

        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}

    async def delete_track(self, track_ref_id: ObjectId) -> bool:
        """Delete track and all its chunks"""
        if not self.connected:
            return False

        try:
            # Delete chunks first
            await self.chunks_collection.delete_many({"track_ref_id": track_ref_id})
            
            # Delete track
            result = await self.tracks_collection.delete_one({"_id": track_ref_id})
            
            logger.info(f"🗑️ Deleted track and chunks: track_ref_id={track_ref_id}")
            return result.deleted_count > 0

        except PyMongoError as e:
            logger.error(f"Failed to delete track: {e}")
            return False

    async def delete_room(self, room_ref_id: ObjectId) -> bool:
        """Delete room and all its tracks and chunks"""
        if not self.connected:
            return False

        try:
            # Get all tracks for this room
            tracks = await self.get_room_tracks(room_ref_id)
            
            # Delete all chunks for all tracks
            for track in tracks:
                await self.chunks_collection.delete_many({"track_ref_id": track["_id"]})
            
            # Delete all tracks
            await self.tracks_collection.delete_many({"room_ref_id": room_ref_id})
            
            # Delete room
            result = await self.rooms_collection.delete_one({"_id": room_ref_id})
            
            logger.info(f"🗑️ Deleted room, tracks and chunks: room_ref_id={room_ref_id}")
            return result.deleted_count > 0

        except PyMongoError as e:
            logger.error(f"Failed to delete room: {e}")
            return False

    # ==========================================================
    # 🏭 SINGLETON PATTERN
    # ==========================================================

    @classmethod
    def get_instance(cls) -> "MongoDBService":
        """Get singleton instance"""
        return cls()

    async def _trigger_summary_api(self, room_id: str):
        """Call Orchestrator API to generate summary for the closed room."""
        try:
            logger.info(f"Triggering summary API for room {room_id}")
            config = get_config()
            orchestrator_url = config.orchestrator.url
            api_key = config.orchestrator.internal_api_key
            
            if not orchestrator_url or not api_key:
                logger.warning("Summary trigger skipped: Orchestrator URL or API Key missing")
                return

            endpoint = f"{orchestrator_url.rstrip('/')}/api/internal/summary"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint,
                    json={"room_id": room_id},
                    headers={
                        "Content-Type": "application/json",
                        "x-internal-api-key": api_key
                    },
                    timeout=10
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Summary triggered successfully for room {room_id}: {data}")
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to trigger summary for room {room_id}: {response.status} - {error_text}")
        except Exception as e:
            logger.error(f"Error triggering summary API for room {room_id}: {e}")


def get_mongodb_service() -> MongoDBService:
    """Convenience function to get MongoDB service instance"""
    return MongoDBService.get_instance()