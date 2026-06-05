"""
PostgreSQL repository for transcripts (replaces MongoDBService).
Handles rooms, tracks, chunks, summary, and metadata events.
"""

import json
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple, Set
import uuid

from sqlalchemy import text
from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class PgTranscriptRepository:
    """PostgreSQL-backed transcript repository. Drop-in for MongoDBService."""

    def __init__(self):
        self.connected = True  # Always "connected" via connection pool

    async def connect(self):
        self.connected = True
        return True

    async def disconnect(self):
        pass

    async def ping(self):
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # ROOMS
    # ------------------------------------------------------------------

    def _build_list_rooms_condition_query(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        condition_query = ""
        params = {}

        if status:
            condition_query += " AND status = :status"
            params["status"] = status
        if from_utc:
            condition_query += " AND created_at >= :from_utc"
            params["from_utc"] = from_utc
        if to_utc:
            condition_query += " AND created_at <= :to_utc"
            params["to_utc"] = to_utc
        if search:
            condition_query += " AND (room_name ILIKE :search OR EXISTS (SELECT 1 FROM jsonb_array_elements(participants) AS p WHERE p->>'participant_identity' ILIKE :search))"
            params["search"] = f"%{search}%"

        return condition_query, params

    async def list_rooms(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
        limit: int = 10,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        session_factory = get_session_factory()

        conditions, params = self._build_list_rooms_condition_query(
            status=status,
            search=search,
            from_utc=from_utc,
            to_utc=to_utc
        )

        query = f"SELECT * FROM rooms WHERE 1=1 {conditions} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        
        params["limit"] = limit
        params["offset"] = skip

        try:
            async with session_factory() as session:
                result = await session.execute(text(query), params)
                rows = result.fetchall()
                rooms = []
                for row in rows:
                    r = dict(row._mapping)
                    rooms.append(r)
                return rooms
        except Exception as e:
            logger.error(f"Failed to list rooms: {e}")
            return []

    async def count_rooms(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> int:
        session_factory = get_session_factory()

        conditions, params = self._build_list_rooms_condition_query(
            status=status,
            search=search,
            from_utc=from_utc,
            to_utc=to_utc
        )

        query = f"SELECT COUNT(*) FROM rooms WHERE 1=1 {conditions}"

        try:
            async with session_factory() as session:
                result = await session.execute(text(query), params)
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"Failed to count rooms: {e}")
            return 0

    async def get_room_by_id(self, room_id: str) -> Optional[Dict[str, Any]]:
        uid = room_id
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("SELECT * FROM rooms WHERE id = :id"), {"id": uid}
                )
                row = result.fetchone()
                if row:
                    r = dict(row._mapping)
                    return r
                return None
        except Exception as e:
            logger.error(f"Failed to get room by id: {e}")
            return None

    async def create_room_session(
        self, room_name: str, status: str = "pending"
    ) -> Optional[str]:
        session_factory = get_session_factory()
        now = datetime.now(timezone.utc)
        uid = str(uuid.uuid4())
        try:
            async with session_factory() as session:
                await session.execute(
                    text("""
                    INSERT INTO rooms (id, room_name, status, participants, created_at)
                    VALUES (:id, :name, :status, CAST('[]' AS jsonb), :now)
                """),
                    {"id": uid, "name": room_name, "status": status, "now": now},
                )
                await session.commit()
            return str(uid)
        except Exception as e:
            logger.error(f"Failed to create room: {e}")
            return None

    async def final_room_status(self, room_name: str, room_id: str) -> bool:
        uid = room_id
        session_factory = get_session_factory()
        now = datetime.now(timezone.utc)
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("""
                    UPDATE rooms SET status = 'final_room', finalized_at = :now
                    WHERE id = :id AND status = 'pending'
                    RETURNING id
                """),
                    {"id": uid, "now": now},
                )
                await session.commit()
                return result.fetchone() is not None
        except Exception as e:
            logger.error(f"Failed to finalize room: {e}")
            return False

    async def save_participant(
        self, room_id: str, participant_identity: str, timestamp: Optional[datetime] = None
    ) -> bool:
        uid = room_id
        session_factory = get_session_factory()
        ts = timestamp or datetime.now(timezone.utc)
        try:
            async with session_factory() as session:
                await session.execute(
                    text("""
                    UPDATE rooms 
                    SET participants = participants || CAST(:p AS jsonb)
                    WHERE id = :id AND NOT (participants @> CAST(:check AS jsonb))
                """),
                    {
                        "id": uid,
                        "p": json.dumps(
                            [
                                {
                                    "participant_identity": participant_identity,
                                    "timestamp": ts.isoformat(),
                                }
                            ]
                        ),
                        "check": json.dumps(
                            [{"participant_identity": participant_identity}]
                        ),
                    },
                )
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save participant: {e}")
            return False

    async def save_batch_participants(
        self, room_id: str, participants: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        if not participants:
            return {"success": True, "added_count": 0, "skipped_count": 0}

        session_factory = get_session_factory()

        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("SELECT participants FROM rooms WHERE id = :id"),
                    {"id": room_id}
                )
                row = result.fetchone()

                if not row:
                    logger.error(f"Room {room_id} not found")
                    return {"success": False, "added_count": 0, "skipped_count": 0}
                
                room = dict(row._mapping) if row else {}
                
                existing_participants = room.get("participants") or []
                
                existing_identities: Set[str] = {
                    p.get("participant_identity")
                    for p in existing_participants
                    if p.get("participant_identity")
                }

                participants_to_add = []
                skipped_count = 0

                for p in participants:
                    identity = p.get("participant_identity")

                    if not identity:
                        skipped_count += 1
                        continue

                    if identity in existing_identities:
                        skipped_count += 1
                        continue

                    ts = p.get("timestamp") or datetime.now(timezone.utc)
                    if isinstance(ts, datetime):
                        ts = ts.isoformat()
                    
                    participants_to_add.append({
                        "participant_identity": identity,
                        "timestamp": ts,
                    })

                if participants_to_add:
                    added_count = len(participants_to_add)

                    await session.execute(
                        text("""
                            UPDATE rooms 
                            SET participants = participants || CAST(:new_participants AS jsonb)
                            WHERE id = :id
                        """),
                        {
                            "id": room_id,
                            "new_participants": json.dumps(participants_to_add)
                        }
                    )
                    await session.commit()
                    logger.info(f"✅ Batch save to room {room_id}: Added {added_count}, Skipped {skipped_count}")

                    return {
                        "success": True,
                        "added_count": added_count,
                        "skipped_count": skipped_count,
                    }
                else:
                    logger.info(f"ℹ️ No new participants to add to room {room_id}")
                    return {
                        "success": True,
                        "added_count": 0,
                        "skipped_count": skipped_count
                    }
        
        except Exception as e:
            logger.error(f"❌ Error in save_batch_participants: {e}")
            return {"success": False, "added_count": 0, "skipped_count": 0}

    async def update_room_participants(
        self, room_id: str, participants: List[Dict[str, Any]]
    ) -> bool:
        uid = room_id
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                await session.execute(
                    text("""
                    UPDATE rooms 
                    SET participants = CAST(:p AS jsonb)
                    WHERE id = :id
                """),
                    {
                        "id": uid,
                        "p": json.dumps(participants),
                    },
                )
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to update room participants: {e}")
            return False

    # ------------------------------------------------------------------
    # TRACKS
    # ------------------------------------------------------------------

    async def get_tracks_by_room(
        self, room_id: str, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            uuid.UUID(room_id)
        except (ValueError, AttributeError):
            logger.warning(f"get_tracks_by_room: invalid UUID format repr={repr(room_id)}, returning empty")
            return []
        uid = room_id
        session_factory = get_session_factory()
        query = "SELECT * FROM tracks WHERE room_ref_id = :rid"
        params = {"rid": uid}
        if status:
            query += " AND status = :status"
            params["status"] = status
        try:
            async with session_factory() as session:
                result = await session.execute(text(query), params)
                rows = result.fetchall()
                tracks = []
                for row in rows:
                    t = dict(row._mapping)
                    t["room_ref_id"] = str(t["room_ref_id"])
                    tracks.append(t)
                return tracks
        except Exception as e:
            logger.error(f"Failed to get tracks by room: {e}")
            return []

    async def get_track_by_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("SELECT * FROM tracks WHERE id = :id"), {"id": track_id}
                )
                row = result.fetchone()
                if row:
                    t = dict(row._mapping)
                    if t.get("room_ref_id"):
                        t["room_ref_id"] = str(t["room_ref_id"])
                    return t
                return None
        except Exception as e:
            logger.error(f"Failed to get track: {e}")
            return None

    async def update_track_status(self, track_ref_id: str, status: str) -> dict:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE tracks SET status = :status, updated_at = :now WHERE id = :id"
                    ),
                    {
                        "status": status,
                        "now": datetime.now(timezone.utc),
                        "id": track_ref_id,
                    },
                )
                await session.commit()
                res = await session.execute(
                    text("SELECT * FROM tracks WHERE id = :id"), {"id": track_ref_id}
                )
                row = res.fetchone()
                if row:
                    t = dict(row._mapping)
                    return {"success": True, "track": t}
                return {"success": False, "error": "Not found"}
        except Exception as e:
            logger.error(f"Failed to update track status: {e}")
            return {"success": False, "error": str(e)}

    async def check_event_record_done(self, room_ref_id: str) -> Optional[dict]:
        uid = room_ref_id
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                room_res = await session.execute(
                    text(
                        "SELECT * FROM rooms WHERE id = :rid AND status = 'final_room'"
                    ),
                    {"rid": uid},
                )
                row = room_res.fetchone()
                if not row:
                    logger.info(
                        f"No room found for room_ref_id={room_ref_id} with status 'final_room'"
                    )
                    return None

                res = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM tracks WHERE room_ref_id = :rid AND status = 'pending'"
                    ),
                    {"rid": uid},
                )
                pending = res.scalar() or 0
                if pending == 0:
                    r = dict(row._mapping)
                    return r
                return None
        except Exception as e:
            logger.error(f"Failed check event record done: {e}")
            return None

    async def check_and_complete_room(self, room_ref_id: str) -> bool:
        uid = room_ref_id
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                res = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM tracks WHERE room_ref_id = :rid AND status IN ('pending', 'wait_process')"
                    ),
                    {"rid": uid},
                )
                pending = res.scalar() or 0
                if pending > 0:
                    logger.debug(f"Room still has {pending} incomplete tracks")
                    return False

                result = await session.execute(
                    text(
                        "UPDATE rooms SET status = 'completed', completed_at = :now WHERE id = :rid AND status = 'final_room'"
                    ),
                    {"rid": uid, "now": datetime.now(timezone.utc)},
                )
                await session.commit()
                return result.rowcount > 0
        except Exception as e:
            logger.error(f"Failed check and complete room: {e}")
            return False

    async def user_has_room_access(self, room_id: str, user_id: str) -> bool:
        session_factory = get_session_factory()

        try:
            query = """
                SELECT 1
                FROM rooms
                WHERE id = :room_id
                AND participants @> CAST(:uid AS jsonb)
                LIMIT 1
            """

            params = {
                "room_id": room_id,
                "uid": json.dumps([{"participant_identity": user_id}])
            }

            async with session_factory() as session:
                result = await session.execute(text(query), params)
                has_access = result.scalar() is not None

                logger.debug(
                    f"user_has_room_access: user_id={user_id}, room_id={room_id}, has_access={has_access}"
                )

                return has_access

        except Exception as e:
            logger.error(f"Failed to check user room access: {e}")
            return False

    async def get_room_statistics_by_id(self, room_id: str) -> Dict[str, Any]:
        uid = room_id
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                room_res = await session.execute(
                    text("SELECT * FROM rooms WHERE id = :rid"), {"rid": uid}
                )
                room_row = room_res.fetchone()
                if not room_row:
                    return {}
                room = dict(room_row._mapping)
                created_at_raw: datetime = room.get("created_at")
                finaled_at_raw: datetime = room.get("finalized_at")
                total_duration_sec: float = 0.0
                if finaled_at_raw and created_at_raw:
                    total_duration_sec = (finaled_at_raw - created_at_raw).total_seconds()

                track_res = await session.execute(
                    text("SELECT status FROM tracks WHERE room_ref_id = :rid"),
                    {"rid": uid},
                )
                tracks = track_res.fetchall()
                completed = sum(1 for t in tracks if t[0] == "completed")
                total = len(tracks)

                sum_res = await session.execute(
                    text(
                        "SELECT total_segments FROM rooms_summary WHERE room_id = :rid LIMIT 1"
                    ),
                    {"rid": uid},
                )
                sum_row = sum_res.fetchone()
                segments = sum_row[0] if sum_row and sum_row[0] else 0

                return {
                    "room_id": str(uid),
                    "room_name": room.get("room_name"),
                    "status": room.get("status"),
                    "total_tracks": total,
                    "completed_tracks": completed,
                    "remaining_tracks": total - completed,
                    "total_segments": segments,
                    "created_at": created_at_raw,
                    "finalized_at": finaled_at_raw if finaled_at_raw else None,
                    "total_duration_sec": total_duration_sec,
                }
        except Exception as e:
            logger.error(f"Failed to get room stats: {e}")
            return {}

    async def list_rooms_by_user(
        self,
        user_id: str,
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
        limit: int = 10,
        skip: int = 0,
    ) -> List[dict]:
        session_factory = get_session_factory()

        conditions, params = self._build_list_rooms_condition_query(
            status=status,
            search=search,
            from_utc=from_utc,
            to_utc=to_utc
        )

        conditions += " AND participants @> CAST(:uid AS jsonb)"
        params["uid"] = json.dumps([{"participant_identity": user_id}])
        
        query = f"SELECT * FROM rooms WHERE 1=1 {conditions} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"

        params["limit"] = limit
        params["offset"] = skip

        try:
            async with session_factory() as session:
                result = await session.execute(text(query), params)
                rows = result.fetchall()
                rooms = []
                for row in rows:
                    r = dict(row._mapping)
                    rooms.append(r)
                return rooms
        except Exception as e:
            logger.error(f"Failed to list rooms by user: {e}")
            return []

    async def count_rooms_by_user(
        self,
        user_id: str,
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> int:
        session_factory = get_session_factory()

        conditions, params = self._build_list_rooms_condition_query(
            status=status,
            search=search,
            from_utc=from_utc,
            to_utc=to_utc
        )

        conditions += " AND participants @> CAST(:uid AS jsonb)"
        params["uid"] = json.dumps([{"participant_identity": user_id}])
        
        query = f"SELECT COUNT(*) FROM rooms WHERE 1=1 {conditions}"

        try:
            async with session_factory() as session:
                result = await session.execute(text(query), params)
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"Failed to list rooms by user: {e}")
            return []

    # ------------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------------

    async def save_room_summary(self, summary_data: Dict[str, Any]) -> Optional[str]:
        session_factory = get_session_factory()
        room_uid = summary_data.get("room_id")
        uid = room_uid
        try:
            async with session_factory() as session:
                await session.execute(
                    text("""
                    INSERT INTO rooms_summary (id, room_id, room_name, participants, summary_data, messages, total_segments, created_at)
                    VALUES (:id, :rid, :rname, CAST(:parts AS jsonb), CAST(:sum AS jsonb), CAST(:msgs AS jsonb), :ts, :now)
                """),
                    {
                        "id": uid,
                        "rid": room_uid,
                        "rname": summary_data.get("room_name"),
                        "parts": json.dumps(summary_data.get("participants", [])),
                        "sum": json.dumps(summary_data.get("summary_data", {})),
                        "msgs": json.dumps(summary_data.get("messages", [])),
                        "ts": summary_data.get("total_segments", 0),
                        "now": summary_data.get(
                            "created_at", datetime.now(timezone.utc)
                        ),
                    },
                )
                await session.commit()
                return str(uid)
        except Exception as e:
            logger.error(f"Failed to save summary: {e}")
            return None

    async def update_room_summary(self, room_id: str, summary_data: Dict[str, Any]) -> bool:
        uid = room_id
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                res = await session.execute(
                    text(
                        "UPDATE rooms_summary SET summary_data = CAST(:sum AS jsonb) WHERE room_id = :rid RETURNING id"
                    ),
                    {"sum": json.dumps(summary_data), "rid": uid},
                )
                await session.commit()
                return res.fetchone() is not None
        except Exception as e:
            logger.error(f"Failed to update summary: {e}")
            return False

    async def get_summary_by_room_id(self, room_id: str) -> Dict[str, Any]:
        uid = room_id
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                room_res = await session.execute(
                    text("SELECT * FROM rooms WHERE id = :id"), {"id": uid}
                )
                room_row = room_res.fetchone()
                if not room_row:
                    return {}
                room = dict(room_row._mapping)
                res = await session.execute(
                    text(
                        "SELECT * FROM rooms_summary WHERE room_id = :rid ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"rid": uid},
                )
                row = res.fetchone()
                if row:
                    s = dict(row._mapping)
                    s["room_id"] = str(s["room_id"])
                    s["created_at"] = room.get("created_at")
                    s["completed_at"] = room.get("completed_at")

                    # Read speech durations directly from the rooms table participants column
                    speech_durations = []
                    room_participants = room.get("participants") or []
                    for p in room_participants:
                        user_id = p.get("participant_identity")
                        if user_id and not user_id.startswith("EG_"):
                            speech_durations.append({
                                "participant_identity": user_id,
                                "duration": p.get("duration", 0.0)
                            })

                    s["speech_durations"] = speech_durations
                    return s
                return {}
        except Exception as e:
            logger.error(f"Failed to get summary by id: {e}")
            return {}

    async def get_summary_by_room_name(
        self,
        room_name: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                # 1. build query for rooms
                r_query = "SELECT * FROM rooms WHERE room_name = :rname"
                r_params = {"rname": room_name}
                if start_time:
                    r_query += " AND created_at >= :st"
                    r_params["st"] = start_time
                if end_time:
                    r_query += " AND created_at <= :et"
                    r_params["et"] = end_time
                if user_id:
                    r_query += " AND participants @> CAST(:uid AS jsonb)"
                    r_params["uid"] = json.dumps([{"participant_identity": user_id}])

                r_query += " ORDER BY created_at ASC"
                r_res = await session.execute(text(r_query), r_params)
                room_list = [dict(r._mapping) for r in r_res.fetchall()]

                if not room_list:
                    return []

                room_dict = {str(r["id"]): r for r in room_list}
                room_ids = tuple([r["id"] for r in room_list])

                # 2. get summaries
                s_query = "SELECT * FROM rooms_summary WHERE room_id = ANY(:rids)"
                s_res = await session.execute(text(s_query), {"rids": list(room_ids)})
                summary_list = [dict(s._mapping) for s in s_res.fetchall()]

                summary_response_list = []

                for summary in summary_list:
                    summary_response = dict(summary)
                    summary_response["room_id"] = str(summary["room_id"])

                    room = room_dict.get(str(summary["room_id"]), {})
                    summary_response["created_at"] = room.get("created_at")
                    summary_response["completed_at"] = room.get("completed_at")
                    summary_response_list.append(summary_response)

                return summary_response_list
        except Exception as e:
            logger.error(f"Failed to get summary by room name: {e}")
            return []

    # ------------------------------------------------------------------
    # METADATA EVENTS
    # ------------------------------------------------------------------

    async def save_metadata_event(self, event_data: Dict[str, Any]) -> Optional[str]:
        session_factory = get_session_factory()
        uid = str(uuid.uuid4())
        room_uid = event_data.get("room_id")
        try:
            async with session_factory() as session:
                await session.execute(
                    text("""
                    INSERT INTO metadata_events (id, event_id, event_type, room_id, room_name, metadata, timestamp, created_at)
                    VALUES (:id, :eid, :etype, :rid, :rname, CAST(:meta AS jsonb), :ts, :now)
                    ON CONFLICT (event_id) DO NOTHING
                """),
                    {
                        "id": uid,
                        "eid": event_data.get("event_id"),
                        "etype": event_data.get("event_type"),
                        "rid": room_uid,
                        "rname": event_data.get("room_name"),
                        "meta": json.dumps(event_data.get("metadata", {})),
                        "ts": str(event_data.get("timestamp")),
                        "now": datetime.now(timezone.utc),
                    },
                )
                await session.commit()
                return str(uid)
        except Exception as e:
            logger.error(f"Failed to save event: {e}")
            return None

    async def get_metadata_event_by_event_id(self, event_id: str) -> Optional[Dict]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                res = await session.execute(
                    text("SELECT * FROM metadata_events WHERE event_id = :eid"),
                    {"eid": event_id},
                )
                row = res.fetchone()
                if row:
                    e = dict(row._mapping)
                    e["room_id"] = str(e["room_id"])
                    return e
                return None
        except Exception as e:
            logger.error(f"Failed to get event by id: {e}")
            return None

    async def append_transcript_chunk(
        self, track_ref_id: str, new_segments: List[Dict[str, Any]]
    ) -> bool:
        """Append new segments as additional chunks"""
        if not new_segments:
            return True

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                # Get last chunk index
                res = await session.execute(
                    text(
                        "SELECT chunk_index FROM transcript_chunks WHERE track_ref_id = :tid ORDER BY chunk_index DESC LIMIT 1"
                    ),
                    {"tid": track_ref_id},
                )
                last_chunk_index = res.scalar()
                start_index = (
                    (last_chunk_index + 1) if last_chunk_index is not None else 0
                )

                # Assume self._split_into_chunks exists or just dump everything if not available
                # Actually, Mongo implementation has self._split_into_chunks and self._create_chunk_document.
                # I'll replicate the logic or just insert them directly.
                # In Mongo, chunks are split. We'll implement a basic splitter here if needed, or just insert as one chunk for now.
                # To match exactly:
                chunk_size = 50
                chunks = [
                    new_segments[i : i + chunk_size]
                    for i in range(0, len(new_segments), chunk_size)
                ]

                chunk_documents = []
                for i, chunk_segments in enumerate(chunks):
                    start_time = (
                        chunk_segments[0].get("start", 0.0) if chunk_segments else 0.0
                    )
                    end_time = (
                        chunk_segments[-1].get("end", 0.0) if chunk_segments else 0.0
                    )
                    uid = str(uuid.uuid4())
                    chunk_documents.append(
                        {
                            "id": uid,
                            "tid": track_ref_id,
                            "idx": start_index + i,
                            "st": start_time,
                            "et": end_time,
                            "cnt": len(chunk_segments),
                            "seg": json.dumps(chunk_segments),
                        }
                    )

                if chunk_documents:
                    await session.execute(
                        text("""
                            INSERT INTO transcript_chunks (id, track_ref_id, chunk_index, start_time, end_time, item_count, segments)
                            VALUES (:id, :tid, :idx, :st, :et, :cnt, CAST(:seg AS jsonb))
                        """),
                        chunk_documents,
                    )

                    await session.execute(
                        text(
                            "UPDATE tracks SET chunk_count = chunk_count + :cnt WHERE id = :tid"
                        ),
                        {"cnt": len(chunk_documents), "tid": track_ref_id},
                    )

                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to append chunks: {e}")
            return False

    def _build_metadata_events_condition_query(
        self,
        event_type: Optional[str] = None,
        room_id: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        condition_query = ""
        params = {}

        if room_id:
            condition_query += " AND room_id = :rid"
            params["rid"] = room_id
        if event_type:
            condition_query += " AND event_type = :etype"
            params["etype"] = event_type
        if from_utc:
            condition_query += " AND created_at >= :from_utc"
            params["from_utc"] = from_utc
        if to_utc:
            condition_query += " AND created_at <= :to_utc"
            params["to_utc"] = to_utc

        return condition_query, params

    async def get_metadata_events(
        self,
        event_type: Optional[str] = None,
        room_id: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
        limit: int = 100,
        skip: int = 0,
        sort_order: str = "desc",
    ) -> List[Dict[str, Any]]:
        session_factory = get_session_factory()
        
        conditions, params = self._build_metadata_events_condition_query(
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc
        )

        order = "ASC" if sort_order.lower() == "asc" else "DESC"
        query = f"SELECT * FROM metadata_events WHERE 1=1 {conditions} ORDER BY created_at {order} LIMIT :limit OFFSET :skip"

        params["limit"] = limit
        params["skip"] = skip

        try:
            async with session_factory() as session:
                res = await session.execute(text(query), params)
                rows = res.fetchall()
                events = []
                for row in rows:
                    e = dict(row._mapping)
                    if e.get("room_id"):
                        e["room_id"] = str(e["room_id"])
                    if isinstance(e.get("created_at"), datetime):
                        e["created_at"] = e["created_at"].isoformat() + "Z"
                    events.append(e)
                return events
        except Exception as e:
            logger.error(f"Failed to get metadata events: {e}")
            return []

    async def count_metadata_events(
        self,
        event_type: Optional[str] = None,
        room_id: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> int:
        session_factory = get_session_factory()
        
        conditions, params = self._build_metadata_events_condition_query(
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc
        )

        query = f"SELECT COUNT(*) FROM metadata_events WHERE 1=1 {conditions}"

        try:
            async with session_factory() as session:
                res = await session.execute(text(query), params)
                return res.scalar() or 0
        except Exception as e:
            logger.error(f"Failed to count metadata events: {e}")
            return 0

    async def get_chunks_by_track(
        self,
        track_id: str,
        sorted_by_index: bool = True,
        limit: int = None,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        session_factory = get_session_factory()
        query = "SELECT * FROM transcript_chunks WHERE track_ref_id = :tid"
        params = {"tid": track_id}

        if sorted_by_index:
            query += " ORDER BY chunk_index ASC"
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = limit
        if skip > 0:
            query += " OFFSET :skip"
            params["skip"] = skip

        try:
            async with session_factory() as session:
                result = await session.execute(text(query), params)
                rows = result.fetchall()
                chunks = []
                for row in rows:
                    c = dict(row._mapping)
                    chunks.append(c)
                return chunks
        except Exception as e:
            logger.error(f"Failed to get chunks by track: {e}")
            return []

    async def get_chunks_by_track_ids(
        self,
        track_ids: List[str],
        sorted_by_index: bool = True,
    ) -> List[Dict[str, Any]]:
        if not track_ids:
            return []

        session_factory = get_session_factory()
        
        # Safely construct IN clause with parameters
        placeholders = ", ".join([f":tid_{i}" for i in range(len(track_ids))])
        query = f"SELECT * FROM transcript_chunks WHERE track_ref_id IN ({placeholders})"
        params = {f"tid_{i}": tid for i, tid in enumerate(track_ids)}

        if sorted_by_index:
            query += " ORDER BY chunk_index ASC"

        try:
            async with session_factory() as session:
                result = await session.execute(text(query), params)
                rows = result.fetchall()
                chunks = []
                for row in rows:
                    c = dict(row._mapping)
                    chunks.append(c)
                return chunks
        except Exception as e:
            logger.error(f"Failed to get chunks by track ids: {e}")
            return []

    async def save_track_metadata(
        self,
        *,
        egress_id: str = None,
        track_id: Optional[str] = None,
        room_ref_id: Optional[str] = None,
        participant_identity: Optional[str] = None,
        audio_info: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        error: Optional[str] = None,
    ) -> Optional[dict]:
        if not egress_id:
            logger.error("egress_id is required")
            return None

        session_factory = get_session_factory()
        room_uid = room_ref_id if room_ref_id else None
        now = datetime.now(timezone.utc)
        audio_json = json.dumps(audio_info) if audio_info else None

        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("SELECT id FROM tracks WHERE id = :id"), {"id": egress_id}
                )
                if result.fetchone():
                    updates = ["updated_at = :now"]
                    params = {"id": egress_id, "now": now}
                    if status:
                        updates.append("status = :status")
                        params["status"] = status
                    if audio_info is not None:
                        updates.append("audio_info = :audio")
                        params["audio"] = audio_json
                    if error is not None:
                        updates.append("error = :error")
                        params["error"] = error

                    await session.execute(
                        text(f"UPDATE tracks SET {', '.join(updates)} WHERE id = :id"),
                        params,
                    )
                else:
                    await session.execute(
                        text("""
                        INSERT INTO tracks (id, track_id, room_ref_id, participant_identity, status, audio_info, error, created_at, updated_at)
                        VALUES (:id, :tid, :rid, :pid, :status, CAST(:audio AS jsonb), :error, :now, :now)
                    """),
                        {
                            "id": egress_id,
                            "tid": track_id,
                            "rid": room_uid,
                            "pid": participant_identity,
                            "status": status,
                            "audio": audio_json,
                            "error": error,
                            "now": now,
                        },
                    )
                await session.commit()

                res = await session.execute(
                    text("SELECT * FROM tracks WHERE id = :id"), {"id": egress_id}
                )
                row = res.fetchone()
                if row:
                    d = dict(row._mapping)
                    if d.get("room_ref_id"):
                        d["room_ref_id"] = str(d["room_ref_id"])
                    logger.info(f"📝 Track metadata saved: id(egress)={egress_id}")
                    return d
                return None
        except Exception as e:
            logger.error(f"Failed to save track metadata: {e}")
            return None

# --------------- Singleton ---------------
_pg_transcript_repository: PgTranscriptRepository | None = None

def get_pg_transcript_repository() -> PgTranscriptRepository:
    global _pg_transcript_repository
    if _pg_transcript_repository is None:
        _pg_transcript_repository = PgTranscriptRepository()
    return _pg_transcript_repository