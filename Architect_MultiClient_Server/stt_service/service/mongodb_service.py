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
import asyncio
import aiohttp
from tts_service.logger import get_logger
from stt_service.config.app_config import get_config
from stt_service.utils.decorator import singleton


logger = get_logger(__name__)


@singleton
class MongoDBService:
    """Service for storing track-based transcripts in MongoDB"""

    CHUNK_SIZE = 50  # Maximum segments per chunk


    def __init__(self):
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

            self.connected = True
            logger.info("✅ Connected to MongoDB with authentication")
            logger.info("ℹ️  Note: Run 'python -m scripts.migrate_mongodb' to create/update indexes")
            return True

        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            self.connected = False
            return False

    async def _verify_indexes(self):
        """
        Verify that required indexes exist (lightweight check).
        
        Note: Index creation/migration should be done via:
            python -m scripts.migrate_mongodb
        
        This method only logs warnings if critical indexes are missing.
        """
        try:
            # Check for critical unique index on transcript_chunks
            chunk_indexes = await self.chunks_collection.index_information()
            has_unique_track_chunk = any(
                idx.get('unique') and 
                idx.get('key') == [('track_ref_id', 1), ('chunk_index', 1)]
                for idx in chunk_indexes.values()
            )
            
            if not has_unique_track_chunk:
                logger.warning(
                    "⚠️  Missing critical index on transcript_chunks. "
                    "Run: python -m scripts.migrate_mongodb"
                )

        except Exception as e:
            logger.debug(f"Index verification skipped: {e}")

    async def disconnect(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("MongoDB disconnected")

    # ==========================================================
    # 🏠 ROOM METHODS
    # ==========================================================


    async def check_and_complete_room(
        self,
        room_ref_id: ObjectId
    ) -> bool:
        """
        Check if room should be completed:
        - Room status must be "final_room"
        - No tracks with status "pending"
        
        If conditions met, update room status to "completed" and trigger summary.
        Uses atomic findOneAndUpdate to prevent race conditions.
        
        Args:
            room_ref_id: Room reference ID
            
        Returns:
            True if room was completed, False otherwise
        """
        if not self.connected:
            return False

        try:
            # Count pending tracks first (lightweight check)
            incomplete_count = await self.tracks_collection.count_documents({
                "room_ref_id": room_ref_id,
                "status": {"$in": ["pending", "wait_process"]}    
            })
            
            # If there are still pending tracks, don't complete
            if incomplete_count > 0:
                logger.debug(f"Room still has {incomplete_count} incomplete tracks")
                return False
            
            # Atomic update: only update if status is "final_room" (prevent race condition)
            # findOneAndUpdate ensures only ONE thread can change status from final_room → completed
            updated_room = await self.rooms_collection.find_one_and_update(
                {
                    "_id": room_ref_id,
                    "status": "final_room"  # Only update if status is still "final_room"
                },
                {
                    "$set": {
                        "status": "completed",
                        "completed_at": datetime.utcnow()
                    }
                },
                return_document=True  # Return updated document
            )
            
            # If updated_room is None, another thread already completed it
            if not updated_room:
                logger.debug(f"Room already completed by another process: room_id={room_ref_id}")
                return False
            
            # This thread won the race - trigger summary
            logger.info(f"🎉 Room completed: room_id={room_ref_id} (all tracks processed)")
            asyncio.create_task(self._trigger_summary_api(str(room_ref_id)))
            
            return True

        except PyMongoError as e:
            logger.error(f"Failed to check and complete room: {e}")
            return False

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
        track_ref_id: str,
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
        track_ref_id: str,
        status: str
    ) -> bool:
        """
        Update track status and automatically update room counters.
        
        Args:
            track_ref_id: Track _id (egress_id string)
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
            room_ref_id = current_track.get("room_ref_id")
            
            # Update track status
            result = await self.tracks_collection.update_one(
                {"_id": track_ref_id},
                {"$set": {"status": status, "updated_at": datetime.utcnow()}}
            )
            
            if result.modified_count == 0:
                logger.warning(f"Track status not modified: track_id={track_ref_id}")
                return False
            
            logger.info(f"📝 Track status updated: {old_status} → {status} (track_id={track_ref_id})")
            
            # Check if room should be completed (if status changed from processing)
            await self.check_and_complete_room(room_ref_id)
            
            return True

        except PyMongoError as e:
            logger.error(f"Failed to update track status: {e}")
            return False
        
    async def append_transcript_chunk(
        self,
        track_ref_id: str,
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
