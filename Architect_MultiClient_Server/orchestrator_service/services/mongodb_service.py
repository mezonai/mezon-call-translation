"""
MongoDB service for storing STT records:
- Rooms Collection: information about rooms and processing status
- Song Collection: metadata of audio tracks (room references)
- Transcript_chunks Collection: segments divided into chunks (maximum 200 items/chunk)
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pymongo import ReturnDocument
import logging
from pymongo.errors import PyMongoError
from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.time_convert import convert_to_iso_8601
from orchestrator_service.models.summary_models import RoomSummaryResponse
logger = logging.getLogger(__name__)


class MongoDBService:
    """Service for storing track-based transcripts in MongoDB"""

    _instance = None

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
        
        # Collection names
        self.rooms_collection_name = "rooms"
        self.tracks_collection_name = "tracks"
        self.chunks_collection_name = "transcript_chunks"
        self.summary_collection_name = "rooms_summary"

        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.rooms_collection = None
        self.tracks_collection = None
        self.chunks_collection = None
        self.summary_collection = None
        self.connected = False
        self._initialized = True

        logger.info(
            f"MongoDBService initialized (DB={self.database_name}, "
            f"Collections={self.rooms_collection_name}, {self.tracks_collection_name}, {self.chunks_collection_name}, {self.summary_collection_name})"
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
            self.summary_collection = self.db[self.summary_collection_name]
            
            # Test connection
            await self.client.admin.command("ping")

            self.connected = True
            logger.info("✅ Connected to MongoDB with authentication")
            return True

        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            self.connected = False
            return False


    async def disconnect(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("MongoDB disconnected")

    # ========================================
    # 📦 ROOM COLLECTION QUERIES (READ ONLY)
    # ========================================

    async def get_room_by_name(self, room_name: str) -> Optional[Dict[str, Any]]:
        """Get room by name"""
        try:
            return await self.rooms_collection.find_one({"room_name": room_name})
        except Exception as e:
            logger.error(f"Failed to get room: {e}")
            return None



    async def get_room_by_id(
        self, room_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get room by _id"""

        try:
            room: dict = await self.rooms_collection.find_one({"_id": ObjectId(room_id)})
            room["_id"] = str(room["_id"])
            room["created_at"] = convert_to_iso_8601(room["created_at"])
            room["completed_at"] = convert_to_iso_8601(room.get("completed_at", None))
            return room

        except Exception as e:
            logger.exception(f"Unexpected error when fetching room by ID {e}")
            raise

    async def list_rooms(self, status: str = None, search: str = None,
                        from_utc: datetime = None, to_utc: datetime = None,
                        limit: int = 100, skip: int = 0) -> List[Dict[str, Any]]:
        """List rooms with optional status, search (room_name or participant_identity), and time range."""
        try:
            query = await self._build_rooms_list_query(status=status, search=search, from_utc=from_utc, to_utc=to_utc)
            cursor = self.rooms_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
            room_list: list[dict] = await cursor.to_list(length=limit)
            for room in room_list:
                room["_id"] = str(room["_id"])
                room["created_at"] = convert_to_iso_8601(room["created_at"])
                room["completed_at"] = convert_to_iso_8601(room.get("completed_at", None))
            return room_list
        except Exception as e:
            logger.error(f"Failed to list rooms: {e}")
            return []

    async def _build_rooms_list_query(
        self,
        status: str = None,
        search: str = None,
        from_utc: datetime = None,
        to_utc: datetime = None,
    ) -> Dict[str, Any]:
        """Build query dict for list_rooms / count_rooms. Search matches room_name or participant_identity in tracks."""
        import re
        and_parts = []
        if status:
            and_parts.append({"status": status})
        if from_utc is not None or to_utc is not None:
            created_at = {}
            if from_utc is not None:
                created_at["$gte"] = from_utc
            if to_utc is not None:
                created_at["$lte"] = to_utc
            and_parts.append({"created_at": created_at})
        if search and search.strip():
            search = search.strip()
            room_ids_from_tracks = []
            try:
                room_ids_from_tracks = await self.tracks_collection.distinct(
                    "room_ref_id",
                    {"participant_identity": search}
                )
            except Exception as e:
                logger.debug(f"Tracks distinct for search failed: {e}")
            if room_ids_from_tracks:
                and_parts.append({
                    "$or": [
                        {"room_name": search},
                        {"_id": {"$in": room_ids_from_tracks}},
                    ]
                })
            else:
                and_parts.append({"room_name": search})
        if not and_parts:
            return {}
        if len(and_parts) == 1:
            return and_parts[0]
        return {"$and": and_parts}

    async def count_rooms(
        self,
        status: str = None,
        search: str = None,
        from_utc: datetime = None,
        to_utc: datetime = None,
    ) -> int:
        """Count rooms with same filters as list_rooms."""
        try:
            query = await self._build_rooms_list_query(status=status, search=search, from_utc=from_utc, to_utc=to_utc)
            return await self.rooms_collection.count_documents(query)
        except Exception as e:
            logger.error(f"Failed to count rooms: {e}")
            return 0


    # ========================================
    # 🎵 TRACK COLLECTION QUERIES (READ ONLY)
    # ========================================

    async def get_track_by_egress_id(self, egress_id: str) -> Optional[Dict[str, Any]]:
        """Get track by egress_id (which is _id)"""
        try:
            return await self.tracks_collection.find_one({"_id": egress_id})
        except Exception as e:
            logger.error(f"Failed to get track: {e}")
            return None

    async def get_track_by_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get track by _id (egress_id string)"""
        try:
            return await self.tracks_collection.find_one({"_id": track_id})
        except Exception as e:
            logger.error(f"Failed to get track by ID: {e}")
            return None

    async def get_tracks_by_room(self, room_id: str, status: str = None) -> List[Dict[str, Any]]:
        """Get all tracks for a room, optionally filtered by status"""
        try:
            query = {"room_ref_id": ObjectId(room_id)}
            if status:
                query["status"] = status
            cursor = self.tracks_collection.find(query).sort("created_at", 1)
            return await cursor.to_list(None)
        except Exception as e:
            logger.error(f"Failed to get tracks by room: {e}")
            return []

    async def get_tracks_by_participant(self, participant_identity: str) -> List[Dict[str, Any]]:
        """Get all tracks for a participant"""
        try:
            cursor = self.tracks_collection.find({"participant_identity": participant_identity}).sort("created_at", -1)
            return await cursor.to_list(None)
        except Exception as e:
            logger.error(f"Failed to get tracks by participant: {e}")
            return []

    async def list_tracks(self, status: str = None, limit: int = 100, 
                         skip: int = 0) -> List[Dict[str, Any]]:
        """List tracks with optional status filter"""
        try:
            query = {"status": status} if status else {}
            cursor = self.tracks_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Failed to list tracks: {e}")
            return []

    async def count_tracks_by_room(self, room_id: str, status: str = None) -> int:
        """Count tracks for a room"""
        try:
            query = {"room_ref_id": ObjectId(room_id)}
            if status:
                query["status"] = status
            return await self.tracks_collection.count_documents(query)
        except Exception as e:
            logger.error(f"Failed to count tracks: {e}")
            return 0

    async def get_tracks_by_date_range(self, start_date: datetime, 
                                      end_date: datetime,
                                      status: str = None,
                                      limit: int = 100,
                                      skip: int = 0) -> List[Dict[str, Any]]:
        """Get tracks created within a date range with optional status filter and pagination"""
        try:
            query = {
                "created_at": {
                    "$gte": start_date,
                    "$lte": end_date
                }
            }
            if status:
                query["status"] = status
            
            cursor = self.tracks_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Failed to get tracks by date range: {e}")
            return []

    async def count_tracks_by_date_range(self, start_date: datetime,
                                        end_date: datetime,
                                        status: str = None) -> int:
        """Count tracks in date range with optional status filter"""
        try:
            query = {
                "created_at": {
                    "$gte": start_date,
                    "$lte": end_date
                }
            }
            if status:
                query["status"] = status
            return await self.tracks_collection.count_documents(query)
        except Exception as e:
            logger.error(f"Failed to count tracks by date range: {e}")
            return 0

    # ========================================
    # 📝 TRANSCRIPT CHUNKS COLLECTION QUERIES (READ ONLY)
    # ========================================

    async def get_chunks_by_track(self, track_id: str, 
                                 sorted_by_index: bool = True,
                                 limit: int = None,
                                 skip: int = 0) -> List[Dict[str, Any]]:
        """Get chunks for a track with optional pagination"""
        try:
            query = {"track_ref_id": track_id}
            cursor = self.chunks_collection.find(query)
            if sorted_by_index:
                cursor = cursor.sort("chunk_index", 1)
            if skip > 0:
                cursor = cursor.skip(skip)
            if limit is not None:
                cursor = cursor.limit(limit)
            return await cursor.to_list(None)
        except Exception as e:
            logger.error(f"Failed to get chunks by track: {e}")
            return []

    async def get_chunk_by_index(self, track_id: str, chunk_index: int) -> Optional[Dict[str, Any]]:
        """Get a specific chunk by track and index"""
        try:
            return await self.chunks_collection.find_one({
                "track_ref_id": track_id,
                "chunk_index": chunk_index
            })
        except Exception as e:
            logger.error(f"Failed to get chunk: {e}")
            return None

    async def get_chunks_by_time_range(self, track_id: str, 
                                      start_time: float, 
                                      end_time: float) -> List[Dict[str, Any]]:
        """Get chunks within a time range"""
        try:
            query = {
                "track_ref_id": track_id,
                "$or": [
                    {"start_time": {"$lte": end_time}, "end_time": {"$gte": start_time}}
                ]
            }
            cursor = self.chunks_collection.find(query).sort("start_time", 1)
            return await cursor.to_list(None)
        except Exception as e:
            logger.error(f"Failed to get chunks by time range: {e}")
            return []

    async def count_chunks_by_track(self, track_id: str) -> int:
        """Count total chunks for a track"""
        try:
            return await self.chunks_collection.count_documents({
                "track_ref_id": track_id
            })
        except Exception as e:
            logger.error(f"Failed to count chunks: {e}")
            return 0

    async def get_full_transcript(self, track_id: str) -> List[Dict[str, Any]]:
        """Get full transcript by combining all chunks"""
        try:
            chunks = await self.get_chunks_by_track(track_id, sorted_by_index=True)
            full_transcript = []
            for chunk in chunks:
                full_transcript.extend(chunk.get("segments", []))
            return full_transcript
        except Exception as e:
            logger.error(f"Failed to get full transcript: {e}")
            return []

    # ==========================================================
    # 🏠 ROOM METHODS
    # ==========================================================

    async def create_room_session(
        self,
        room_name: str,
        status: str = "pending",
    ) -> Optional[ObjectId]:
        
        if not self.connected and not await self.connect():
            logger.error("Cannot create room: MongoDB not connected")
            return None
        try:
            room_data = {
                "room_name": room_name,
                "status": status,
                "created_at": datetime.utcnow(),
            }
            
            result = await self.rooms_collection.insert_one(room_data)
            logger.info(f"📁 Room created: room={room_name}, _id={result.inserted_id}")
            return result.inserted_id
            
        except PyMongoError as e:
            logger.error(f"Failed to create room: {e}")
            return None




    async def final_room_status(self, room_name: str, room_id: str) -> bool:
        """
        Mark room as finalized.
        Only updates if current status is 'pending' (prevents overwriting 'completed').
        Uses atomic findOneAndUpdate to prevent race conditions.
        
        Returns:
            True if status was updated to final_room, False if already finalized/completed
        """
        try:
            room_id = ObjectId(room_id)
            
            # Atomic update: only set final_room if status is currently "pending"
            # This prevents overwriting "completed" status
            updated_room = await self.rooms_collection.find_one_and_update(
                {
                    "_id": room_id,
                    "status": "pending"  # Only update if still pending
                },
                {
                    "$set": {
                        "status": "final_room",
                        "finalized_at": datetime.utcnow()
                    }
                },
                return_document=True
            )
            
            if updated_room:
                logger.info(f"🔒 Room finalized: {room_name} → final_room")
                return True
            else:
                return False

        except PyMongoError as e:
            logger.error(f"Failed to finalize room: {e}")
            return False


    async def check_event_record_done(
        self,
        room_ref_id: str
    ) -> Optional[dict]:
        """
        Count pending tracks in a room 
        only if room is in final_room status.
        """
        try:
            # Check room exists AND is in final_room status
            room = await self.rooms_collection.find_one(
                {"_id": ObjectId(room_ref_id), "status": "final_room"}
            )
            if not room:
                logger.info(f"No room found for room_ref_id={room_ref_id} with status 'final_room'")
                return None

            logger.info(f"found room: {room.get('_id')} with status: {room.get('status')}")

            # Count pending tracks
            count = await self.tracks_collection.count_documents({
                "room_ref_id": ObjectId(room_ref_id),
                "status": "pending"
            })

            if count == 0:
                return room
            else:
                return None
        except PyMongoError as e:
            logger.error(f"Failed to count pending tracks: {e}")
            return None

    async def check_and_complete_room(
        self,
        room_ref_id: str
    ) -> bool:
        """
        Check if room should be completed:
        - Room status must be "pending" or "final_room" (not already completed)
        - No tracks with status "pending" or "wait_process"
        
        If conditions met, update room status to "completed".
        Uses atomic findOneAndUpdate to prevent race conditions.
        
        Args:
            room_ref_id: Room reference ID
            
        Returns:
            True if room was completed by THIS call, False otherwise
        """

        try:
            room_ref_id = ObjectId(room_ref_id)
            
            # Count pending and wait_process tracks first (lightweight check)
            incomplete_count = await self.tracks_collection.count_documents({
                "room_ref_id": room_ref_id,
                "status": {"$in": ["pending", "wait_process"]}    
            })
            
            # If there are still pending tracks, don't complete
            if incomplete_count > 0:
                logger.debug(f"Room still has {incomplete_count} incomplete tracks")
                return False
            
            # Atomic update: only update if status is "pending" OR "final_room"
            # This allows completion even if final_room() hasn't been called yet
            # findOneAndUpdate ensures only ONE thread can complete the room
            updated_room = await self.rooms_collection.find_one_and_update(
                {
                    "_id": room_ref_id,
                    "status": "final_room"  # Accept both states
                },
                {
                    "$set": {
                        "status": "completed",
                        "completed_at": datetime.utcnow()
                    }
                },
                return_document=True  # Return updated document
            )
            
            # If updated_room is None, room already completed
            if not updated_room:
                logger.debug(f"Room already completed: room_id={room_ref_id}")
                return False
            
            # This thread won the race
            logger.info(f"🎉 Room completed: room_id={room_ref_id} (all tracks processed)")
            return True

        except PyMongoError as e:
            logger.error(f"Failed to check and complete room: {e}")
            return False

    # ==========================================================
    # 🔥 TRACK METHODS (Updated to use room_ref_id)
    # ==========================================================

    async def save_track_metadata(
        self,
        *,
        egress_id: str,
        track_id: Optional[str] = None,
        room_ref_id: Optional[ObjectId] = None,
        participant_identity: Optional[str] = None,
        audio_info: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        error: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Save track metadata using egress_id as _id.
        
        Args:
            egress_id: Unique egress identifier (used as _id)
            track_id: Track identifier
            room_ref_id: Reference to room document _id
            participant_identity: Participant identity
            audio_info: Dict containing {filename, ...}
            status: Track status (default: "pending")
            error: Optional dict containing error details if track processing failed
        Returns:
            docs of inserted/updated document, or None if failed
        """

        try:
            
            # Build $set operation - only include audio_info if provided
            set_fields = {
                "status": status,
                "updated_at": datetime.utcnow()
            }
            if audio_info is not None:
                set_fields["audio_info"] = audio_info
            
            if error is not None:
                set_fields["error"] = error

            result = await self.tracks_collection.find_one_and_update(
                {"_id": egress_id},
                {
                    "$setOnInsert": {
                        "_id": egress_id,
                        "track_id": track_id,
                        "room_ref_id": room_ref_id,
                        "participant_identity": participant_identity,
                        "chunk_count": 0,
                        "created_at": datetime.utcnow(),
                    },
                    "$set": set_fields
                },
                upsert=True,
                return_document=ReturnDocument.AFTER
            )
            
            if result:
                logger.info(
                    f"📝 Track metadata saved: _id(egress)={egress_id} "
                )
                
                return result
            else:
                logger.warning(f"Track metadata operation returned None: egress={egress_id}")
                return None

        except PyMongoError as e:
            logger.error(f"Failed to save track metadata: {e}")
            return None




    # ========================================
    # 📊 ANALYTICS & STATISTICS QUERIES
    # ========================================

    async def get_room_statistics_by_id(self, room_id: str) -> Dict[str, Any]:
        """Get detailed statistics for a room by ID"""
        try:
            # Fetch raw room document to preserve datetime types for calculations
            room = await self.rooms_collection.find_one({"_id": ObjectId(room_id)})
            if not room:
                return {}

            created_at_raw: datetime = room.get("created_at")
            completed_at_raw: datetime = room.get("completed_at")
            total_duration_sec: float = 0.0
            if completed_at_raw and created_at_raw:
                total_duration_sec = (completed_at_raw - created_at_raw).total_seconds()
            tracks = await self.get_tracks_by_room(room_id)
            total_segments = 0
            completed_tracks = 0
            remain_tracks = 0
            for track in tracks:
                chunks = await self.get_chunks_by_track(str(track["_id"]))
                for chunk in chunks:
                    total_segments += chunk.get("item_count", 0)
                if track.get("status") == "completed":
                    completed_tracks += 1
                else:
                    remain_tracks += 1

            return {
                "room_id": room_id,
                "room_name": room.get("room_name"),
                "status": room.get("status"),
                "total_tracks": len(tracks),
                "completed_tracks": completed_tracks,
                "remain_tracks": remain_tracks,
                "total_duration_sec": total_duration_sec,
                "total_segments": total_segments,
                "created_at": convert_to_iso_8601(created_at_raw),
                "completed_at": convert_to_iso_8601(completed_at_raw) if completed_at_raw else None
            }
        except Exception as e:
            logger.error(f"Failed to get room statistics by ID: {e}")
            return {}

    async def get_participant_statistics(self, participant_identity: str) -> Dict[str, Any]:
        """Get statistics for a participant across all rooms"""
        try:
            tracks = await self.get_tracks_by_participant(participant_identity)
            
            total_duration = 0
            total_segments = 0
            rooms = set()
            
            for track in tracks:
                rooms.add(str(track.get("room_ref_id")))
                
                chunks = await self.get_chunks_by_track(str(track["_id"]))
                for chunk in chunks:
                    total_segments += chunk.get("item_count", 0)
                
                audio_info = track.get("audio_info", {})
                duration_ns = int(audio_info.get("duration_sec", "0"))
                total_duration += duration_ns / 1_000_000_000
            
            return {
                "participant_identity": participant_identity,
                "total_tracks": len(tracks),
                "unique_rooms": len(rooms),
                "total_duration_sec": total_duration,
                "total_segments": total_segments
            }
        except Exception as e:
            logger.error(f"Failed to get participant statistics: {e}")
            return {}

    # ========================================
    # 📝 ROOM SUMMARY QUERIES
    # ========================================

    async def save_room_summary(self, summary_data: Dict[str, Any]) -> str:
        """Save or update room summary"""
        try:
            room_id = summary_data.get("room_id")
            if not room_id:
                return None
                
            result = await self.summary_collection.update_one(
                {"room_id": room_id},
                {"$set": summary_data},
                upsert=True
            )
            
            if result.upserted_id:
                return str(result.upserted_id)
            
            # If updated an existing document, we need to find its ID
            if result.matched_count > 0:
                doc = await self.summary_collection.find_one({"room_id": room_id}, {"_id": 1})
                if doc:
                    return str(doc["_id"])
                    
            return None
        except Exception as e:
            logger.error(f"Failed to save room summary: {e}")
            return None


    async def get_summary_by_room_name(self, room_name: str, start_time: Optional[datetime], end_time: Optional[datetime]) -> List[RoomSummaryResponse]:
        """Get summary by room name"""
        try:
            # 1. get room list
            query = {"room_name": room_name}
            if start_time or end_time:
                query["created_at"] = {}
                if start_time:
                    query["created_at"]["$gte"] = start_time
                if end_time:
                    query["created_at"]["$lte"] = end_time
            cursor = self.rooms_collection.find(query).sort("created_at", 1)
            room_list: list[dict] = await cursor.to_list(None)
            room_dict = {str(room["_id"]): room for room in room_list}
            room_ids = [str(room["_id"]) for room in room_list]

            # 2. get summary list
            summary_list = await self.summary_collection.find(
                {"room_id": {"$in": room_ids}}
            ).to_list(None)

            # Override created_at and completed_at
            summary_response_list = []
            for summary in summary_list:
                summary_response = RoomSummaryResponse.model_construct(**summary)
                created_at = room_dict.get(str(summary["room_id"])).get("created_at", "")
                completed_at = room_dict.get(str(summary["room_id"])).get("completed_at", "")
                summary_response.created_at = convert_to_iso_8601(created_at)
                summary_response.completed_at = convert_to_iso_8601(completed_at)
                summary_response_list.append(summary_response)
            return summary_response_list
        except Exception as e:
            logger.error(f"Failed to get summary by room name: {e}")
            return []

    async def get_summary_by_room_id(self, room_id: str) -> RoomSummaryResponse:
        """Get summary by room id"""
        try:
            summary_data: dict = await self.summary_collection.find_one({"room_id": room_id})
            response = RoomSummaryResponse()
            if summary_data:
                response = RoomSummaryResponse.model_construct(**summary_data)
            room_data: dict = await self.rooms_collection.find_one({"_id": ObjectId(room_id)})
            response.created_at = convert_to_iso_8601(room_data.get("created_at", ""))
            response.completed_at = convert_to_iso_8601(room_data.get("completed_at", ""))
            return response
        except Exception as e:
            logger.error(f"Failed to get summary by room id: {e}")
            return []

    # ========================================
    # 🏭 SINGLETON PATTERN
    # ========================================

    @classmethod
    def get_instance(cls) -> "MongoDBService":
        """Get singleton instance"""
        return cls()


def get_mongodb_service() -> MongoDBService:
    """Convenience function to get MongoDB service instance"""
    return MongoDBService.get_instance()