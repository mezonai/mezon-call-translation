"""
MongoDB service for storing STT transcripts (1 document = 1 audio track)
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
import logging
from stt_service.config.app_config import get_config

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
        mongo_host = self.config.mongodb.host
        mongo_port = self.config.mongodb.port
        mongo_username = self.config.mongodb.username
        mongo_password = self.config.mongodb.password
        
        # MongoDB URI format with authentication
        # mongodb://username:password@host:port/?authSource=admin
        self.mongo_uri = (
            f"mongodb://{mongo_username}:{mongo_password}@"
            f"{mongo_host}:{mongo_port}/?authSource=admin"
        )
        
        self.database_name = self.config.mongodb.database
        self.collection_name = self.config.mongodb.collection

        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.collection = None
        self.connected = False

        self._initialized = True
        logger.info(
            f"MongoDBService initialized (DB={self.database_name}, Collection={self.collection_name})"
        )

    async def connect(self) -> bool:
        if self.connected:
            return True

        try:
            # Connect with authentication
            self.client = AsyncIOMotorClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=5000,  # 5 second timeout
                connectTimeoutMS=10000,         # 10 second timeout
            )
            
            self.db = self.client[self.database_name]
            self.collection = self.db[self.collection_name]

            # Test connection with ping
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
        """Indexes optimized for STT & playback"""
        try:
            await self.collection.create_index("egress_id", unique=True)
            await self.collection.create_index("track_id")
            await self.collection.create_index("room_name")
            await self.collection.create_index("participant_identity")
            await self.collection.create_index("created_at")

            logger.info("✅ MongoDB indexes created")

        except Exception as e:
            logger.warning(f"Failed to create indexes: {e}")

    async def disconnect(self):
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("MongoDB disconnected")

    # ==========================================================
    # 🔥 CORE METHOD
    # ==========================================================

    async def save_track_transcript(
        self,
        *,
        egress_id: str,
        track_id: str,
        room_name: str,
        participant_identity: str,
        audio: Dict[str, Any],
        transcript: Dict[str, Any],
        status: str = "completed",
    ) -> Optional[str]:
        """
        Save transcript for ONE audio track (after STT finished)

        audio = {
            filename,
            duration_sec,
            started_at_ns,
            ended_at_ns
        }

        transcript = {
            language,
            segments: [...]
        }
        """

        if not self.connected and not await self.connect():
            return None

        document = {
            "egress_id": egress_id,
            "track_id": track_id,
            "room_name": room_name,
            "participant_identity": participant_identity,

            "audio": {
                "filename": audio["filename"],
                "duration_sec": audio["duration_sec"],
                "started_at_ns": audio["started_at_ns"],
                "ended_at_ns": audio["ended_at_ns"],
            },

            "transcript": {
                "language": transcript.get("language"),
                "segments": transcript.get("segments", []),
            },

            "status": status,
            "created_at": datetime.utcnow(),
        }

        try:
            result = await self.collection.insert_one(document)
            logger.info(
                f"📝 Transcript saved: "
                f"egress={egress_id}, track={track_id}, segments={len(document['transcript']['segments'])}"
            )
            return str(result.inserted_id)

        except PyMongoError as e:
            logger.error(f"MongoDB insert failed: {e}")
            return None

    # ==========================================================
    # 🔍 QUERY HELPERS
    # ==========================================================

    async def get_by_egress_id(self, egress_id: str) -> Optional[Dict]:
        if not self.connected:
            return None
        return await self.collection.find_one({"egress_id": egress_id})

    async def get_room_transcripts(self, room_name: str) -> List[Dict]:
        if not self.connected:
            return []
        return await self.collection.find(
            {"room_name": room_name}
        ).sort("created_at", 1).to_list(None)

    async def get_stats(self) -> Dict[str, Any]:
        if not self.connected:
            return {}

        return {
            "total_tracks": await self.collection.count_documents({}),
            "total_rooms": len(await self.collection.distinct("room_name")),
            "database": self.database_name,
            "collection": self.collection_name,
        }

    @classmethod
    def get_instance(cls) -> "MongoDBService":
        return cls()


def get_mongodb_service() -> MongoDBService:
    return MongoDBService.get_instance()