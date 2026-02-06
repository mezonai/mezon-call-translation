import json
from fastapi import APIRouter, Request, HTTPException
from datetime import datetime

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.auth.webhook_auth import verify_webhook, is_verification_enabled
from orchestrator_service.services.egress_service import EgressService
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.controller.webhook_handler import WebhookHandler
from orchestrator_service.models.webhook_models import WebhookResponse
from livekit import api

router = APIRouter()
logger = get_logger(__name__)

# Singleton services
egress_service = EgressService()
transcription_service = TranscriptionService()
webhook_handler = WebhookHandler(egress_service, transcription_service)


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "service": "livekit-webhook-handler",
        "active_egresses": egress_service.get_active_count(),
        "timestamp":  datetime.utcnow()
    }


@router.get("/egresses")
async def list_active_egresses():
    """List active egresses"""
    return {
        "status": "ok",
        "active_egresses": egress_service.get_all_active(),
        "count": egress_service.get_active_count()
    }


@router.post("/webhook", response_model=WebhookResponse)
async def handle_webhook(request: Request):
    """
    Handle webhook events từ LiveKit
    
    Webhook verification:
    - LIVEKIT_VERIFY_WEBHOOKS=true: enable signature verification (default)
    - LIVEKIT_VERIFY_WEBHOOKS=false: skip verification (dev only)
    """
    try:
        body = await request.body()
        body_str = body.decode("utf-8")
        auth_header = request.headers.get("Authorization", "")
        
        # Verify signature
        if is_verification_enabled():
            is_valid, error = verify_webhook(body_str, auth_header)
            if not is_valid:
                logger.warning(f"⚠️ Verification failed: {error}")
                raise HTTPException(
                    status_code=401, 
                    detail=f"Verification failed: {error}"
                )
            logger.debug("✓ Signature verified")
        else:
            logger.debug("⚠️ Verification disabled")
        
        event = json.loads(body_str)
        return await webhook_handler.handle_event(event)
        
    except json.JSONDecodeError as e:
        logger.error(f"✗ Invalid JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"✗ Error processing webhook: {e}")
        return WebhookResponse(received=False, error=str(e))

# This api use for testing purposes only
@router.post("/egress/stop/{track_sid}")  
async def stop_egress(track_sid: str):
    """Manually stop egress cho track"""
    if track_sid not in egress_service.get_all_active():
        raise HTTPException(404, f"No active egress for {track_sid}")
    
    success = await egress_service.stop_recording(track_sid)
    if success:
        return {"status": "stopped", "track_sid": track_sid}
    raise HTTPException(500, "Failed to stop egress")

# This api use for testing purposes only
@router.post("/egress/stop-all")
async def stop_all_egresses():
    """Stop tất cả egress"""
    if egress_service.get_active_count() == 0:
        return {"status": "ok", "message": "No active egresses", "stopped": 0}
    
    result = await egress_service.stop_all()
    return {
        "status": "ok",
        **result,
        "remaining": egress_service.get_active_count()
    }