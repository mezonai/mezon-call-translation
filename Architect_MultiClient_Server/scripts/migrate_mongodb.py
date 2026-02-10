"""
MongoDB Migration Script

Run this script to create/update database indexes and perform other migrations.
This should be run once after deployment or when database schema changes.

Usage:
    python -m scripts.migrate_mongodb
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path to import stt_service modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient
from stt_service.config.app_config import get_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class MongoDBMigration:
    """Handle MongoDB migrations"""
    
    def __init__(self):
        self.config = get_config()
        self.mongo_uri = self._build_mongo_uri()
        self.database_name = self.config.mongodb.database
        self.client = None
        self.db = None
        
    def _build_mongo_uri(self) -> str:
        """Build MongoDB connection URI with authentication"""
        mongo_config = self.config.mongodb
        return (
            f"mongodb://{mongo_config.username}:{mongo_config.password}@"
            f"{mongo_config.host}:{mongo_config.port}/?authSource=admin"
        )
    
    async def connect(self) -> bool:
        """Establish connection to MongoDB"""
        try:
            logger.info(f"Connecting to MongoDB: {self.config.mongodb.host}:{self.config.mongodb.port}")
            self.client = AsyncIOMotorClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            
            self.db = self.client[self.database_name]
            
            # Test connection
            await self.client.admin.command("ping")
            logger.info(f"✅ Connected to MongoDB database: {self.database_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            return False
    
    async def disconnect(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")
    
    async def create_indexes(self):
        """Create indexes for all collections"""
        logger.info("=" * 60)
        logger.info("Creating/updating indexes...")
        logger.info("=" * 60)
        
        try:
            # Get collections
            rooms_collection = self.db["rooms"]
            tracks_collection = self.db["tracks"]
            chunks_collection = self.db["transcript_chunks"]
            
            # ========================================
            # ROOMS COLLECTION INDEXES
            # ========================================
            logger.info("\n📁 Creating indexes for 'rooms' collection...")
            
            # Single field indexes
            await rooms_collection.create_index("room_name")
            logger.info("  ✓ Index: room_name")
            
            await rooms_collection.create_index("status")
            logger.info("  ✓ Index: status")
            
            # Compound indexes
            await rooms_collection.create_index(
                [("status", 1), ("created_at", -1)]
            )
            logger.info("  ✓ Index: (status, created_at) - for filtered+sorted queries")
            
            await rooms_collection.create_index(
                [("room_name", 1), ("created_at", -1)]
            )
            logger.info("  ✓ Index: (room_name, created_at) - for room history queries")
            
            # ========================================
            # TRACKS COLLECTION INDEXES
            # ========================================
            logger.info("\n🎵 Creating indexes for 'tracks' collection...")
            
            # Single field indexes
            await tracks_collection.create_index("track_id")
            logger.info("  ✓ Index: track_id")
            
            await tracks_collection.create_index("participant_identity")
            logger.info("  ✓ Index: participant_identity")
            
            await tracks_collection.create_index("status")
            logger.info("  ✓ Index: status")
            
            # Compound indexes
            await tracks_collection.create_index(
                [("room_ref_id", 1), ("created_at", 1)]
            )
            logger.info("  ✓ Index: (room_ref_id, created_at) - for room tracks sorted")
            
            await tracks_collection.create_index(
                [("room_ref_id", 1), ("status", 1)]
            )
            logger.info("  ✓ Index: (room_ref_id, status) - for filtered room tracks")
            
            await tracks_collection.create_index(
                [("participant_identity", 1), ("created_at", -1)]
            )
            logger.info("  ✓ Index: (participant_identity, created_at) - for participant history")
            
            await tracks_collection.create_index(
                [("status", 1), ("created_at", -1)]
            )
            logger.info("  ✓ Index: (status, created_at) - for status filter + sort")
            
            # ========================================
            # TRANSCRIPT_CHUNKS COLLECTION INDEXES
            # ========================================
            logger.info("\n📝 Creating indexes for 'transcript_chunks' collection...")
            
            await chunks_collection.create_index("track_ref_id")
            logger.info("  ✓ Index: track_ref_id")
            
            await chunks_collection.create_index(
                [("track_ref_id", 1), ("chunk_index", 1)],
                unique=True
            )
            logger.info("  ✓ Index: (track_ref_id, chunk_index) [UNIQUE] - prevents duplicates")
            
            await chunks_collection.create_index(
                [("track_ref_id", 1), ("start_time", 1)]
            )
            logger.info("  ✓ Index: (track_ref_id, start_time) - for time range queries")
            
            logger.info("\n✅ All indexes created successfully!")
            logger.info(f"   Total collections indexed: 3 (rooms, tracks, chunks)")
            
        except Exception as e:
            logger.error(f"\n❌ Failed to create indexes: {e}")
            raise
    
    async def list_indexes(self):
        """List all existing indexes"""
        logger.info("=" * 60)
        logger.info("Current indexes in database")
        logger.info("=" * 60)
        
        try:
            collections = ["rooms", "tracks", "transcript_chunks"]
            
            for collection_name in collections:
                collection = self.db[collection_name]
                indexes = await collection.index_information()
                
                logger.info(f"\n📊 Collection: {collection_name}")
                logger.info(f"   Total indexes: {len(indexes)}")
                for index_name, index_info in indexes.items():
                    keys = index_info.get('key', [])
                    unique = index_info.get('unique', False)
                    unique_str = " [UNIQUE]" if unique else ""
                    logger.info(f"   • {index_name}: {keys}{unique_str}")
                    
        except Exception as e:
            logger.error(f"❌ Failed to list indexes: {e}")
    
    async def drop_old_indexes(self):
        """Drop deprecated indexes (for cleanup)"""
        logger.info("=" * 60)
        logger.info("Cleaning up old indexes...")
        logger.info("=" * 60)
        
        try:
            rooms_collection = self.db["rooms"]
            tracks_collection = self.db["tracks"]
            
            # List of old indexes to drop (if they exist)
            old_room_indexes = [
                "start_session_time_1",  # Removed field
                "room_name_1_start_session_time_1"  # Compound with removed field
            ]
            
            old_track_indexes = [
                "egress_id_1"  # Old egress_id index
            ]
            
            # Drop old room indexes
            for index_name in old_room_indexes:
                try:
                    await rooms_collection.drop_index(index_name)
                    logger.info(f"  ✓ Dropped old index from rooms: {index_name}")
                except Exception:
                    logger.debug(f"  • Index '{index_name}' not found in rooms (already removed)")
            
            # Drop old track indexes
            for index_name in old_track_indexes:
                try:
                    await tracks_collection.drop_index(index_name)
                    logger.info(f"  ✓ Dropped old index from tracks: {index_name}")
                except Exception:
                    logger.debug(f"  • Index '{index_name}' not found in tracks (already removed)")
            
            logger.info("✅ Old indexes cleanup completed")
            
        except Exception as e:
            logger.error(f"❌ Failed to drop old indexes: {e}")
    
    async def get_stats(self):
        """Display database statistics"""
        logger.info("=" * 60)
        logger.info("Database Statistics")
        logger.info("=" * 60)
        
        try:
            rooms_collection = self.db["rooms"]
            tracks_collection = self.db["tracks"]
            chunks_collection = self.db["transcript_chunks"]
            
            total_rooms = await rooms_collection.count_documents({})
            total_tracks = await tracks_collection.count_documents({})
            total_chunks = await chunks_collection.count_documents({})
            
            logger.info(f"\n📊 Collection Counts:")
            logger.info(f"  • Rooms:             {total_rooms:,}")
            logger.info(f"  • Tracks:            {total_tracks:,}")
            logger.info(f"  • Transcript Chunks: {total_chunks:,}")
            
            # Room status breakdown
            room_stats = await rooms_collection.aggregate([
                {"$group": {"_id": "$status", "count": {"$sum": 1}}}
            ]).to_list(None)
            
            if room_stats:
                logger.info(f"\n📈 Room Status Breakdown:")
                for stat in room_stats:
                    logger.info(f"  • {stat['_id']:15} {stat['count']:,}")
            
            # Track status breakdown
            track_stats = await tracks_collection.aggregate([
                {"$group": {"_id": "$status", "count": {"$sum": 1}}}
            ]).to_list(None)
            
            if track_stats:
                logger.info(f"\n🎵 Track Status Breakdown:")
                for stat in track_stats:
                    logger.info(f"  • {stat['_id']:15} {stat['count']:,}")
            
            # Chunk statistics
            chunk_stats = await chunks_collection.aggregate([
                {
                    "$group": {
                        "_id": None,
                        "avg_segments": {"$avg": "$item_count"},
                        "total_segments": {"$sum": "$item_count"}
                    }
                }
            ]).to_list(1)
            
            if chunk_stats:
                logger.info(f"\n📝 Chunk Statistics:")
                logger.info(f"  • Total Segments:    {chunk_stats[0]['total_segments']:,}")
                logger.info(f"  • Avg Segments/Chunk: {chunk_stats[0]['avg_segments']:.1f}")
            
        except Exception as e:
            logger.error(f"❌ Failed to get stats: {e}")
    
    async def run_all_migrations(self):
        """Run all migration tasks"""
        logger.info("\n" + "=" * 60)
        logger.info("🚀 Starting MongoDB Migration")
        logger.info("=" * 60)
        
        if not await self.connect():
            logger.error("Cannot proceed without database connection")
            return False
        
        try:
            # 1. Drop old deprecated indexes first
            await self.drop_old_indexes()
            
            # 2. Create/update indexes
            await self.create_indexes()
            
            # 3. Show current indexes
            await self.list_indexes()
            
            # 4. Display statistics
            await self.get_stats()
            
            logger.info("\n" + "=" * 60)
            logger.info("🎉 Migration completed successfully!")
            logger.info("=" * 60)
            return True
            
        except Exception as e:
            logger.error(f"\n❌ Migration failed: {e}")
            return False
        finally:
            await self.disconnect()


async def main():
    """Main entry point"""
    migration = MongoDBMigration()
    
    # Run all migrations
    success = await migration.run_all_migrations()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())