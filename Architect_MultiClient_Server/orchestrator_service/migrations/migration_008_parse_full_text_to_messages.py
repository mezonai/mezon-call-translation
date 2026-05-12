"""
Migration: Parse full_text to create messages array

This migration:
- Parses full_text from rooms_summary documents
- Creates a messages array with structured message objects
- Each message contains: timestamp (HH:MM:SS), participant_id, and content
"""
import re
from motor.motor_asyncio import AsyncIOMotorDatabase

from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class ParseFullTextToMessages(MigrationBase):
    """Parse full_text field and create messages array in rooms_summary collection."""

    BATCH_SIZE = 100

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db)

    @property
    def migration_id(self) -> str:
        return "008_parse_full_text_to_messages"

    @property
    def description(self) -> str:
        return "Parse full_text from rooms_summary to create structured messages array"

    def _parse_full_text(self, full_text: str) -> list:
        """
        Parse full_text string into messages array.
        
        Format: [HH:MM:SS] participant_id: message content
        Multi-line content is joined by newlines until next message timestamp.
        
        Args:
            full_text: The full text to parse
            
        Returns:
            List of message objects with timestamp, participant_id, and content
        """
        if not full_text:
            return []
        
        # Pattern for message start: [HH:MM:SS] participant_id: content
        # Example: [14:47:29] agent-e7e1b7c2-2b6e-4e2a-9c1d-7f8e2a1b2c3d: Hello, world
        header_pattern = re.compile(r'^\[(\d{2}:\d{2}:\d{2})\]\s+([^:]+):\s*(.*)$')
        
        lines = full_text.split('\n')
        messages = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            match = header_pattern.match(line)
            if match:
                # New message starts with [HH:MM:SS] format
                timestamp = match.group(1)
                participant_id = match.group(2).strip()
                content = match.group(3).strip()
                
                messages.append({
                    "timestamp": timestamp,
                    "participant_id": participant_id,
                    "content": content
                })
            elif messages:
                # This line doesn't have timestamp prefix, 
                # so it's continuation of the previous message
                last_msg = messages[-1]
                if last_msg["content"]:
                    last_msg["content"] += "\n" + line
                else:
                    last_msg["content"] = line
        
        return messages

    async def up(self) -> bool:
        """Apply the migration to parse full_text into messages."""
        try:
            summary_collection = self.db["rooms_summary"]
            
            # Count documents that need migration (have full_text but no messages)
            logger.info("🔍 Counting rooms_summary documents to process...")
            total_docs = await summary_collection.count_documents({
                "full_text": {"$exists": True, "$ne": None, "$ne": ""},
                "$or": [
                    {"messages": {"$exists": False}},
                    {"messages": None},
                    {"messages": []}
                ]
            })
            
            logger.info(f"📊 Found {total_docs} rooms_summary documents to process")
            
            if total_docs == 0:
                logger.info("ℹ️  No documents to process")
                return True
            
            # Process documents in batches
            updated_count = 0
            failed_count = 0
            batch_number = 0
            
            cursor = summary_collection.find({
                "full_text": {"$exists": True, "$ne": None, "$ne": ""},
                "$or": [
                    {"messages": {"$exists": False}},
                    {"messages": None},
                    {"messages": []}
                ]
            }).batch_size(self.BATCH_SIZE)
            
            async for doc in cursor:
                try:
                    room_id = doc.get("_id")
                    full_text = doc.get("full_text", "")
                    room_name = doc.get("room_name", "Unknown")
                    
                    # Parse full_text into messages
                    messages = self._parse_full_text(full_text)
                    
                    if messages:
                        # Update document with parsed messages and remove full_text
                        result = await summary_collection.update_one(
                            {"_id": room_id},
                            {
                                "$set": {"messages": messages},
                                "$unset": {"full_text": ""}
                            }
                        )
                        
                        if result.modified_count > 0:
                            logger.debug(
                                f"✅ Updated room {room_name} ({room_id}) "
                                f"with {len(messages)} messages, removed full_text"
                            )
                            updated_count += 1
                        else:
                            logger.warning(f"⚠️  Failed to update room {room_name} ({room_id})")
                            failed_count += 1
                    else:
                        logger.warning(
                            f"⚠️  No messages parsed for room {room_name} ({room_id}) "
                            f"(full_text might be empty or in unexpected format)"
                        )
                        failed_count += 1
                    
                    # Log progress every batch
                    if (updated_count + failed_count) % self.BATCH_SIZE == 0:
                        batch_number += 1
                        progress = ((updated_count + failed_count) / total_docs) * 100
                        logger.info(
                            f"📈 Progress: {updated_count + failed_count}/{total_docs} "
                            f"({progress:.1f}%) - Batch {batch_number} - "
                            f"Updated: {updated_count}, Failed: {failed_count}"
                        )
                
                except Exception as e:
                    failed_count += 1
                    room_id = doc.get("_id", "Unknown")
                    logger.error(f"❌ Failed to process room {room_id}: {e}")
            
            logger.info(
                f"✅ Completed migration: Updated {updated_count} rooms_summary documents"
            )
            if failed_count > 0:
                logger.warning(f"⚠️  Failed to process {failed_count} documents")
            
            return True
        
        except Exception as e:
            logger.error(f"❌ Migration failed: {e}")
            return False

    def _messages_to_full_text(self, messages: list) -> str:
        """
        Convert messages array back to full_text format.
        
        Args:
            messages: List of message objects with timestamp, participant_id, and content
            
        Returns:
            Full text string in format [HH:MM:SS] participant_id: content
        """
        if not messages:
            return ""
        
        lines = []
        for msg in messages:
            timestamp = msg.get("timestamp", "")
            participant_id = msg.get("participant_id", "")
            content = msg.get("content", "")
            
            # Format: [HH:MM:SS] participant_id: content
            message_text = f"[{timestamp}] {participant_id}: {content}"
            lines.append(message_text)
        
        return "\n".join(lines)

    async def down(self) -> bool:
        """
        Rollback: convert messages array back to full_text format and restore it.
        """
        try:
            summary_collection = self.db["rooms_summary"]
            
            logger.info("🔄 Rolling back: converting messages back to full_text...")
            
            # Find all documents with messages field
            cursor = summary_collection.find(
                {"messages": {"$exists": True, "$ne": None, "$ne": []}}
            ).batch_size(self.BATCH_SIZE)
            
            updated_count = 0
            failed_count = 0
            
            async for doc in cursor:
                try:
                    room_id = doc.get("_id")
                    messages = doc.get("messages", [])
                    room_name = doc.get("room_name", "Unknown")
                    
                    # Convert messages back to full_text
                    full_text = self._messages_to_full_text(messages)
                    
                    if full_text:
                        # Update document with full_text and remove messages
                        result = await summary_collection.update_one(
                            {"_id": room_id},
                            {
                                "$set": {"full_text": full_text},
                                "$unset": {"messages": ""}
                            }
                        )
                        
                        if result.modified_count > 0:
                            logger.debug(
                                f"✅ Restored full_text for room {room_name} ({room_id}), "
                                f"removed messages"
                            )
                            updated_count += 1
                        else:
                            logger.warning(f"⚠️  Failed to restore room {room_name} ({room_id})")
                            failed_count += 1
                    else:
                        logger.warning(
                            f"⚠️  No full_text generated for room {room_name} ({room_id})"
                        )
                        failed_count += 1
                
                except Exception as e:
                    failed_count += 1
                    room_id = doc.get("_id", "Unknown")
                    logger.error(f"❌ Failed to rollback room {room_id}: {e}")
            
            logger.info(
                f"✅ Rollback completed: Restored {updated_count} rooms_summary documents"
            )
            if failed_count > 0:
                logger.warning(f"⚠️  Failed to rollback {failed_count} documents")
            
            return True
        
        except Exception as e:
            logger.error(f"❌ Rollback failed: {e}")
            return False
