"""
API v1 router - includes all endpoints
"""
from fastapi import APIRouter

from orchestrator_service.api.v2.endpoints.dispatch_api import router as dispatch_router
from orchestrator_service.api.v2.endpoints.sse_transcript_api import router as stream_router
from orchestrator_service.api.v2.endpoints.sse_chat_external_api import router as sse_chat_external_router
from orchestrator_service.api.v2.endpoints.sse_metadata_api import router as sse_metadata_router
from orchestrator_service.api.v2.endpoints.sse_agent_request_api import router as sse_agent_request_router
from orchestrator_service.api.v2.endpoints.room_api import router as room_router
from orchestrator_service.api.v2.endpoints.track_api import router as track_router
from orchestrator_service.api.v2.endpoints.transcript_api import router as transcript_router
from orchestrator_service.api.v2.endpoints.room_registry_api import router as room_registry_router
from orchestrator_service.api.v2.endpoints.queue_api import router as queue_router
from orchestrator_service.api.v2.endpoints.auth_api import router as auth_router
from orchestrator_service.api.v2.endpoints.summary_api import client_router as summary_client_router

api_router = APIRouter()

# Include routers
api_router.include_router(auth_router)  # Mezon OAuth2 authentication
api_router.include_router(dispatch_router)
api_router.include_router(stream_router, tags=["sse transcript"])
api_router.include_router(sse_chat_external_router, tags=["sse chat external"])
api_router.include_router(sse_metadata_router, tags=["sse metadata"])
api_router.include_router(sse_agent_request_router, tags=["sse agent requests"])
api_router.include_router(queue_router)  # Has prefix="/api/queue"
api_router.include_router(room_router)  # Has prefix="/api/transcripts/rooms"
api_router.include_router(track_router)  # Has prefix="/api/transcripts/tracks"
api_router.include_router(transcript_router)  # Has prefix="/api/transcripts"
api_router.include_router(room_registry_router)  # Has prefix="/api/room-registry"
api_router.include_router(summary_client_router)


__all__ = ["api_router"]
