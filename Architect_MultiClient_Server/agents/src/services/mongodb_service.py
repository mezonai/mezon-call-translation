"""
MongoDB service for storing transcripts
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
from src.logger import get_logger
from src.config.application_config import get_config

logger = get_logger(__name__)


class MongoDBService:
    """Service for storing transcripts in MongoDB"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MongoDBService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # MongoDB configuration from centralized config
        config = get_config()
        self.mongo_uri = config.mongodb.uri
        self.database_name = config.mongodb.database
        self.collection_name = config.mongodb.collection
        
        # Client and collections
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.collection = None
        
        # Connection state
        self.connected = False
        
        self._initialized = True
        logger.info(f"MongoDBService initialized (URI: {self.mongo_uri}, DB: {self.database_name})")
    
    async def connect(self):
        """Connect to MongoDB"""
        if self.connected:
            return True
        
        try:
            # Create async MongoDB client
            self.client = AsyncIOMotorClient(self.mongo_uri)
            self.db = self.client[self.database_name]
            self.collection = self.db[self.collection_name]
            
            # Test connection
            await self.client.admin.command('ping')
            
            self.connected = True
            logger.info(f"✅ Connected to MongoDB: {self.database_name}.{self.collection_name}")
            
            # Create indexes for better query performance
            await self._create_indexes()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            self.connected = False
            return False
    
    async def _create_indexes(self):
        """Create indexes for efficient queries"""
        try:
            # Index on session_id and timestamp for session queries
            await self.collection.create_index([
                ("session_id", 1),
                ("timestamp", -1)
            ])
            
            # Index on participant_identity
            await self.collection.create_index("participant_identity")
            
            # Index on is_final for filtering
            await self.collection.create_index("is_final")
            
            logger.info("✅ MongoDB indexes created")
            
        except Exception as e:
            logger.warning(f"Failed to create indexes: {e}")
    
    async def disconnect(self):
        """Disconnect from MongoDB"""
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("Disconnected from MongoDB")
    
    async def save_transcript(
        self,
        session_id: str,
        participant_identity: str,
        participant_name: str,
        text: str,
        is_final: bool = True,
        segments: Optional[List[Dict]] = None,
        language: Optional[str] = None,
        seq: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Save transcript to MongoDB
        
        Args:
            session_id: Meeting/room ID
            participant_identity: Participant's unique identity
            participant_name: Participant's display name
            text: Transcript text
            is_final: Whether this is final transcript
            segments: Transcript segments with timing
            language: Language code
            seq: Sequence number
            metadata: Additional metadata
        
        Returns:
            str: Document ID if successful, None otherwise
        """
        if not self.connected:
            logger.warning("MongoDB not connected, attempting to connect...")
            if not await self.connect():
                logger.error("Cannot save transcript: MongoDB connection failed")
                return None
        
        try:
            # Prepare document
            document = {
                "session_id": session_id,
                "participant_identity": participant_identity,
                "participant_name": participant_name,
                "text": text,
                "is_final": is_final,
                "segments": segments or [],
                "language": language,
                "seq": seq,
                "timestamp": datetime.utcnow(),
                "metadata": metadata or {}
            }
            
            # Insert document
            result = await self.collection.insert_one(document)
            
            logger.debug(
                f"📝 Saved transcript to MongoDB: "
                f"session={session_id}, participant={participant_identity}, "
                f"is_final={is_final}, doc_id={result.inserted_id}"
            )
            
            return str(result.inserted_id)
            
        except PyMongoError as e:
            logger.error(f"MongoDB error saving transcript: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error saving transcript: {e}")
            return None
    
    async def get_session_transcripts(
        self,
        session_id: str,
        only_final: bool = True,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all transcripts for a session
        
        Args:
            session_id: Session/meeting ID
            only_final: Only return final transcripts
            limit: Maximum number of results
        
        Returns:
            List of transcript documents
        """
        if not self.connected:
            logger.warning("MongoDB not connected")
            return []
        
        try:
            # Build query
            query = {"session_id": session_id}
            if only_final:
                query["is_final"] = True
            
            # Execute query with sorting
            cursor = self.collection.find(query).sort("timestamp", 1)
            
            if limit:
                cursor = cursor.limit(limit)
            
            # Convert to list
            transcripts = await cursor.to_list(length=limit)
            
            logger.debug(f"Retrieved {len(transcripts)} transcripts for session {session_id}")
            return transcripts
            
        except Exception as e:
            logger.error(f"Error retrieving transcripts: {e}")
            return []
    
    async def get_participant_transcripts(
        self,
        session_id: str,
        participant_identity: str,
        only_final: bool = True
    ) -> List[Dict[str, Any]]:
        """Get transcripts for a specific participant in a session"""
        if not self.connected:
            return []
        
        try:
            query = {
                "session_id": session_id,
                "participant_identity": participant_identity
            }
            if only_final:
                query["is_final"] = True
            
            transcripts = await self.collection.find(query).sort("timestamp", 1).to_list(None)
            return transcripts
            
        except Exception as e:
            logger.error(f"Error retrieving participant transcripts: {e}")
            return []
    
    async def delete_session_transcripts(self, session_id: str) -> int:
        """
        Delete all transcripts for a session
        
        Returns:
            Number of deleted documents
        """
        if not self.connected:
            return 0
        
        try:
            result = await self.collection.delete_many({"session_id": session_id})
            logger.info(f"Deleted {result.deleted_count} transcripts for session {session_id}")
            return result.deleted_count
            
        except Exception as e:
            logger.error(f"Error deleting transcripts: {e}")
            return 0
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        if not self.connected:
            return {}
        
        try:
            stats = {
                "total_transcripts": await self.collection.count_documents({}),
                "total_sessions": len(await self.collection.distinct("session_id")),
                "total_participants": len(await self.collection.distinct("participant_identity")),
                "database_name": self.database_name,
                "collection_name": self.collection_name,
                "connected": self.connected
            }
            return stats
            
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"error": str(e)}
    
    @classmethod
    def get_instance(cls) -> 'MongoDBService':
        """Get singleton instance"""
        return cls()


# Global instance getter
def get_mongodb_service() -> MongoDBService:
    """Get MongoDB service instance"""
    return MongoDBService.get_instance()
