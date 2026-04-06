"""
Migration: Add participants field and index to rooms collection
"""
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class AddParticipantsToRooms(MigrationBase):
    """
    Migration to add participants field to rooms collection and create unique index.
    Populates participants data from tracks collection for existing rooms.
    Ensures each participant_identity appears only once per room.
    """

    # Batch size for processing rooms
    BATCH_SIZE = 100

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db)

    @property
    def migration_id(self) -> str:
        return "005_add_participants_to_rooms"

    @property
    def description(self) -> str:
        return "Add participants field to rooms with unique participant_identity, populate from tracks, and create index"

    async def up(self) -> bool:
        """
        Apply the migration:
        1. Add participants field to existing rooms by aggregating from tracks (unique participants)
        2. Create index on participants.participant_identity
        """
        try:
            rooms_collection = self.db["rooms"]
            tracks_collection = self.db["tracks"]

            # Step 1: Count total rooms to update
            logger.info("🔍 Counting rooms without participants field...")
            total_rooms = await rooms_collection.count_documents(
                {"participants": {"$exists": False}}
            )
            logger.info(f"📊 Found {total_rooms} rooms to update")

            if total_rooms == 0:
                logger.info("ℹ️  No rooms to update")
            else:
                # Step 2: Process rooms in batches using cursor
                updated_count = 0
                failed_count = 0
                batch_number = 0

                cursor = rooms_collection.find(
                    {"participants": {"$exists": False}}
                ).batch_size(self.BATCH_SIZE)

                async for room in cursor:
                    room_id = room["_id"]
                    
                    try:
                        # Aggregate tracks to get UNIQUE participants with earliest timestamp
                        # The $group ensures uniqueness by participant_identity
                        pipeline = [
                            {
                                "$match": {
                                    "room_ref_id": room_id,
                                    "participant_identity": {"$exists": True, "$ne": None}
                                }
                            },
                            {
                                "$group": {
                                    "_id": "$participant_identity",  # This ensures uniqueness
                                    "earliest_timestamp": {"$min": "$created_at"}
                                }
                            },
                            {
                                "$project": {
                                    "_id": 0,
                                    "participant_identity": "$_id",
                                    "timestamp": "$earliest_timestamp"
                                }
                            },
                            {
                                "$sort": {"timestamp": 1}
                            }
                        ]

                        participants = await tracks_collection.aggregate(pipeline).to_list(length=None)

                        # Double-check uniqueness (defensive programming)
                        seen_identities = set()
                        unique_participants = []
                        for p in participants:
                            identity = p["participant_identity"]
                            if identity not in seen_identities:
                                seen_identities.add(identity)
                                unique_participants.append(p)
                            else:
                                logger.warning(f"⚠️  Duplicate participant_identity found in room {room_id}: {identity}")

                        # Update room with unique participants array
                        if unique_participants:
                            await rooms_collection.update_one(
                                {"_id": room_id},
                                {"$set": {"participants": unique_participants}}
                            )
                            logger.debug(f"✅ Updated room {room.get('room_name', room_id)} with {len(unique_participants)} unique participants")
                        else:
                            # No tracks found, set empty participants array
                            await rooms_collection.update_one(
                                {"_id": room_id},
                                {"$set": {"participants": []}}
                            )
                            logger.debug(f"⚠️  Room {room.get('room_name', room_id)} has no tracks, set empty participants")

                        updated_count += 1

                        # Log progress every batch
                        if updated_count % self.BATCH_SIZE == 0:
                            batch_number += 1
                            progress = (updated_count / total_rooms) * 100
                            logger.info(f"📈 Progress: {updated_count}/{total_rooms} ({progress:.1f}%) - Batch {batch_number}")

                    except Exception as e:
                        failed_count += 1
                        logger.error(f"❌ Failed to update room {room_id}: {e}")

                logger.info(f"✅ Updated {updated_count} rooms")
                if failed_count > 0:
                    logger.warning(f"⚠️  Failed to update {failed_count} rooms")

            # Step 3: Create index on participants.participant_identity
            logger.info("🔧 Creating index on participants.participant_identity...")
            
            # Check if index already exists
            existing_indexes = await rooms_collection.list_indexes().to_list(length=None)
            index_exists = any(
                idx.get("name") == "participants.participant_identity_1" 
                for idx in existing_indexes
            )

            if not index_exists:
                # Create index for fast lookup
                # Note: This is NOT a unique constraint across documents,
                # but allows efficient queries like: 
                # db.rooms.find({"participants.participant_identity": "12345"})
                await rooms_collection.create_index(
                    "participants.participant_identity",
                    name="participants.participant_identity_1"
                )
                logger.info("✅ Index created successfully")
            else:
                logger.info("ℹ️  Index already exists, skipping creation")

            # Step 4: Validate uniqueness in participants arrays
            logger.info("🔍 Validating uniqueness of participant_identity in all rooms...")
            validation_pipeline = [
                {
                    "$match": {
                        "participants": {"$exists": True, "$ne": []}
                    }
                },
                {
                    "$project": {
                        "room_name": 1,
                        "participant_count": {"$size": "$participants"},
                        "unique_count": {"$size": {"$setUnion": ["$participants.participant_identity", []]}}
                    }
                },
                {
                    "$match": {
                        "$expr": {"$ne": ["$participant_count", "$unique_count"]}
                    }
                }
            ]
            
            rooms_with_duplicates = await rooms_collection.aggregate(validation_pipeline).to_list(length=None)
            
            if rooms_with_duplicates:
                logger.warning(f"⚠️  Found {len(rooms_with_duplicates)} rooms with duplicate participants:")
                for room in rooms_with_duplicates[:10]:  # Show first 10
                    logger.warning(f"   - Room {room.get('room_name', room['_id'])}: {room['participant_count']} total, {room['unique_count']} unique")
            else:
                logger.info("✅ All rooms have unique participant_identity values")

            logger.info("✅ Migration completed successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Migration failed: {e}")
            return False

    async def down(self) -> bool:
        """
        Rollback the migration:
        1. Drop index on participants.participant_identity
        2. Remove participants field from all rooms
        """
        try:
            rooms_collection = self.db["rooms"]

            # Step 1: Drop index
            logger.info("🔧 Dropping index on participants.participant_identity...")
            try:
                await rooms_collection.drop_index("participants.participant_identity_1")
                logger.info("✅ Index dropped successfully")
            except Exception as e:
                logger.warning(f"⚠️  Failed to drop index (might not exist): {e}")

            # Step 2: Remove participants field from all rooms in batches
            logger.info("🔄 Removing participants field from rooms...")
            
            total_count = 0
            batch_number = 0
            
            # Process in batches to avoid timeout
            while True:
                result = await rooms_collection.update_many(
                    {"participants": {"$exists": True}},
                    {"$unset": {"participants": ""}},
                )
                
                total_count += result.modified_count
                
                if result.modified_count == 0:
                    break
                    
                batch_number += 1
                logger.info(f"📈 Batch {batch_number}: Removed participants from {result.modified_count} rooms (Total: {total_count})")
            
            logger.info(f"✅ Removed participants field from {total_count} rooms total")
            logger.info("✅ Rollback completed successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Rollback failed: {e}")
            return False