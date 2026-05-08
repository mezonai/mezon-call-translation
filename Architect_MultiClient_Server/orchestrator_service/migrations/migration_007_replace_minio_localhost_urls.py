"""
Migration: Replace localhost/minio URLs in tracks.audio_info.location
"""

from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config

logger = get_logger(__name__)


class ReplaceMinioLocalhostURLs(MigrationBase):
    @property
    def migration_id(self) -> str:
        return "007_replace_minio_localhost_urls"

    @property
    def description(self) -> str:
        return (
            "Replace localhost/minio URLs in tracks.audio_info.location "
            "with public IP/domain"
        )

    async def up(self) -> bool:
        """
        Replace:
        - http://minio:9000
        - http://localhost:9000

        -> https://your-domain.com
        """
        
        try:
            target_endpoint = get_config().minio.endpoint
            tracks_collection = self.db["tracks"]
            replacements = [
                {
                    "old": "http://minio:9000",
                    "new": target_endpoint,
                },
                {
                    "old": "http://localhost:9000",
                    "new": target_endpoint,
                },
            ]

            total_updated = 0

            for replacement in replacements:
                cursor = tracks_collection.find({
                    "audio_info.location": {
                        "$regex": f"^{replacement['old']}"
                    }
                })

                async for track in cursor:
                    old_location = track.get("audio_info", {}).get("location")

                    if not old_location:
                        continue

                    new_location = old_location.replace(
                        replacement["old"],
                        replacement["new"],
                        1,
                    )

                    result = await tracks_collection.update_one(
                        {"_id": track["_id"]},
                        {
                            "$set": {
                                "audio_info.location": new_location
                            }
                        },
                    )

                    if result.modified_count > 0:
                        total_updated += 1

                        logger.info(
                            "Updated track %s location: %s -> %s",
                            track["_id"],
                            old_location,
                            new_location,
                        )

            logger.info(
                "✅ Migration completed. Total updated documents: %s",
                total_updated,
            )

            return True

        except Exception as exc:
            logger.error(
                "❌ Migration failed: %s",
                exc,
                exc_info=True,
            )
            return False

    async def down(self) -> bool:
        """
        Rollback URLs back to internal minio hostname.
        """

        try:
            target_endpoint = get_config().minio.endpoint
            tracks_collection = self.db["tracks"]

            replacements = [
                {
                    "old": target_endpoint,
                    "new": "http://minio:9000",
                },
            ]

            total_updated = 0

            for replacement in replacements:
                cursor = tracks_collection.find({
                    "audio_info.location": {
                        "$regex": f"^{replacement['old']}"
                    }
                })

                async for track in cursor:
                    old_location = track.get("audio_info", {}).get("location")

                    if not old_location:
                        continue

                    new_location = old_location.replace(
                        replacement["old"],
                        replacement["new"],
                        1,
                    )

                    result = await tracks_collection.update_one(
                        {"_id": track["_id"]},
                        {
                            "$set": {
                                "audio_info.location": new_location
                            }
                        },
                    )

                    if result.modified_count > 0:
                        total_updated += 1

            logger.info(
                "↩️ Rollback completed. Total updated documents: %s",
                total_updated,
            )

            return True

        except Exception as exc:
            logger.error(
                "❌ Rollback failed: %s",
                exc,
                exc_info=True,
            )
            return False