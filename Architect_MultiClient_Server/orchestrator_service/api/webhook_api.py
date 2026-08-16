import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.auth.webhook_auth import is_verification_enabled, verify_webhook
from orchestrator_service.controller.webhook_handler import WebhookHandler
from orchestrator_service.models.webhook_models import WebhookResponse
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

# Singleton services
transcription_service = TranscriptionService()
webhook_handler = WebhookHandler(transcription_service)


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "service": "livekit-webhook-handler",
        "timestamp": datetime.utcnow(),
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
                raise HTTPException(status_code=401, detail=f"Verification failed: {error}")
            logger.debug("✓ Signature verified")
        else:
            logger.debug("⚠️ Verification disabled")

        event = json.loads(body_str)
        return await webhook_handler.handle_event(event)

    except json.JSONDecodeError as e:
        logger.error(f"✗ Invalid JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from e
    except Exception as e:
        logger.error(f"✗ Error processing webhook: {e}")
        return WebhookResponse(received=False, error=str(e))


@router.post("/internal", response_model=WebhookResponse)
async def handle_internal_webhook(request: Request, auth: dict[str, str | bool] = Depends(verify_api_key)):
    """
    Handle internal webhook events (e.g. relayed from agents).
    """
    try:
        body = await request.body()
        body_str = body.decode("utf-8")

        event = json.loads(body_str)
        return await webhook_handler.handle_event(event)

    except json.JSONDecodeError as e:
        logger.error(f"✗ Invalid JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from e
    except Exception as e:
        logger.error(f"✗ Error processing webhook: {e}")
        return HTTPException(status_code=500, detail=str(e))
