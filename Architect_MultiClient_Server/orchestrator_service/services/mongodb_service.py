"""
MongoDB service for storing STT records:
- Rooms Collection: information about rooms and processing status
- Song Collection: metadata of audio tracks (room references)
- Transcript_chunks Collection: segments divided into chunks (maximum 200 items/chunk)
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError, DuplicateKeyError
from bson import ObjectId
import logging
import sys
import os

from orchestrator_service.config.application_config import get_config

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
            await self.rooms_collection.create_index("room_name", unique=True)
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

    async def get_room_by_id(self, room_id: str) -> Optional[Dict[str, Any]]:
        """Get room by ObjectId"""
        try:
            return await self.rooms_collection.find_one({"_id": ObjectId(room_id)})
        except Exception as e:
            logger.error(f"Failed to get room by ID: {e}")
            return None

    async def list_rooms(self, status: str = None, limit: int = 100, 
                        skip: int = 0) -> List[Dict[str, Any]]:
        """List rooms with optional status filter"""
        try:
            query = {"status": status} if status else {}
            cursor = self.rooms_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Failed to list rooms: {e}")
            return []

    async def count_rooms_by_status(self, status: str = None) -> int:
        """Count rooms by status"""
        try:
            query = {"status": status} if status else {}
            return await self.rooms_collection.count_documents(query)
        except Exception as e:
            logger.error(f"Failed to count rooms: {e}")
            return 0

    async def get_rooms_by_date_range(self, start_date: datetime, 
                                     end_date: datetime, 
                                     status: str = None,
                                     limit: int = 100,
                                     skip: int = 0) -> List[Dict[str, Any]]:
        """Get rooms created within a date range with optional status filter and pagination"""
        try:
            query = {
                "created_at": {
                    "$gte": start_date,
                    "$lte": end_date
                }
            }
            if status:
                query["status"] = status
            
            cursor = self.rooms_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Failed to get rooms by date range: {e}")
            return []

    async def count_rooms_by_date_range(self, start_date: datetime,
                                       end_date: datetime,
                                       status: str = None) -> int:
        """Count rooms in date range with optional status filter"""
        try:
            query = {
                "created_at": {
                    "$gte": start_date,
                    "$lte": end_date
                }
            }
            if status:
                query["status"] = status
            return await self.rooms_collection.count_documents(query)
        except Exception as e:
            logger.error(f"Failed to count rooms by date range: {e}")
            return 0

    # ========================================
    # 🎵 TRACK COLLECTION QUERIES (READ ONLY)
    # ========================================

    async def get_track_by_egress_id(self, egress_id: str) -> Optional[Dict[str, Any]]:
        """Get track by egress_id"""
        try:
            return await self.tracks_collection.find_one({"egress_id": egress_id})
        except Exception as e:
            logger.error(f"Failed to get track: {e}")
            return None

    async def get_track_by_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get track by ObjectId"""
        try:
            return await self.tracks_collection.find_one({"_id": ObjectId(track_id)})
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
        except Exception as e:
            logger.error(f"Failed to get tracks by date range: {e}")
            return []

    # ========================================
    # 📝 TRANSCRIPT CHUNKS COLLECTION QUERIES (READ ONLY)
    # ========================================

    async def get_chunks_by_track(self, track_id: str, 
                                 sorted_by_index: bool = True,
                                 limit: int = None,
                                 skip: int = 0) -> List[Dict[str, Any]]:
        """Get chunks for a track with optional pagination"""
        try:
            query = {"track_ref_id": ObjectId(track_id)}
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
                "track_ref_id": ObjectId(track_id),
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
                "track_ref_id": ObjectId(track_id),
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
                "track_ref_id": ObjectId(track_id)
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


    # ========================================
    # 📊 ANALYTICS & STATISTICS QUERIES
    # ========================================

    async def get_room_statistics(self, room_name: str) -> Dict[str, Any]:
        """Get detailed statistics for a room"""
        try:
            room = await self.get_room_by_name(room_name)
            if not room:
                return {}
            
            tracks = await self.get_tracks_by_room(str(room["_id"]))
            
            total_duration = 0
            total_segments = 0
            
            for track in tracks:
                chunks = await self.get_chunks_by_track(str(track["_id"]))
                for chunk in chunks:
                    total_segments += chunk.get("item_count", 0)
                
                audio_info = track.get("audio_info", {})
                duration_ns = int(audio_info.get("duration_sec", "0"))
                total_duration += duration_ns / 1_000_000_000  # Convert to seconds
            
            return {
                "room_name": room_name,
                "status": room.get("status"),
                "total_tracks": len(tracks),
                "completed_tracks": room.get("completed_tracks", 0),
                "remain_tracks": room.get("remain_tracks", 0),
                "total_duration_sec": total_duration,
                "total_segments": total_segments,
                "created_at": room.get("created_at"),
                "completed_at": room.get("completed_at")
            }
        except Exception as e:
            logger.error(f"Failed to get room statistics: {e}")
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
    # 🏭 SINGLETON PATTERN
    # ========================================

    @classmethod
    def get_instance(cls) -> "MongoDBService":
        """Get singleton instance"""
        return cls()


def get_mongodb_service() -> MongoDBService:
    """Convenience function to get MongoDB service instance"""
    return MongoDBService.get_instance()