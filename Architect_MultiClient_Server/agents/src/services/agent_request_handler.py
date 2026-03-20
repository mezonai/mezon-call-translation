"""
Agent Request Handler

Handles requests received via SSE from orchestrator.
Provides handlers for different request types and integrates with agent services.
"""
import json
import time
import logging
import uuid
from typing import Any, Dict

from src.models.agent_request_type import AgentRequestType


class AgentRequestHandler:
    """Handle agent requests received via SSE from orchestrator."""
    
    def __init__(self):
        """Initialize the agent request handler."""
        self.logger = logging.getLogger(__name__)
    
    async def handle_transcript_control(
        self,
        payload: Dict[str, Any],
        control_state=None,
        event_handlers=None
    ) -> bool:
        """
        Handle transcript control request (enable/disable).
        
        Args:
            payload: Request payload containing action and params
            control_state: AgentControlState instance
            event_handlers: EventHandlers instance
        
        Returns:
            True if handled successfully
        """
        try:
            action = payload.get("action")
            
            self.logger.info(
                f"[Agent Request Handler] Transcript control: action={action}"
            )
            
            if not control_state or not event_handlers:
                self.logger.warning(
                    "[Agent Request Handler] Control state or event handlers not available, "
                    "cannot perform transcript control"
                )
                return False
            
            if action == "enable":
                # Enable transcription
                changed = await control_state.set_transcription_enabled(True)
                started = await event_handlers.start_transcription_for_all_pending()
                
                self.logger.info(
                    f"[Agent Request Handler] Transcription enabled: "
                    f"changed={changed}, started={started} tracks"
                )
                return True
            
            elif action == "disable":
                # Disable transcription
                changed = await control_state.set_transcription_enabled(False)
                stopped = await event_handlers.stop_transcription_for_all()
                
                self.logger.info(
                    f"[Agent Request Handler] Transcription disabled: "
                    f"changed={changed}, stopped={stopped} tracks"
                )
                return True
            
            else:
                self.logger.warning(f"[Agent Request Handler] Unknown action: {action}")
                return False
        
        except Exception as e:
            self.logger.error(
                f"[Agent Request Handler] Error handling transcript control: {e}",
                exc_info=True
            )
            return False
    
    async def handle_tts_play(
        self,
        payload: Dict[str, Any],
        tts_manager=None
    ) -> bool:
        """
        Handle TTS play request.
        
        Args:
            payload: Request payload containing text and params
            tts_manager: TTSManager instance (if available)
        
        Returns:
            True if handled successfully
        """
        try:
            text = payload.get("text")
            sender_identity = payload.get("sender_identity", "orchestrator")
            
            self.logger.info(
                f"[Agent Request Handler] TTS play: text='{text[:50] if text else ''}...', "
                f"sender={sender_identity}"
            )
            
            if not tts_manager:
                self.logger.warning(
                    "[Agent Request Handler] TTSManager not available, cannot play TTS"
                )
                return False
            
            if not text:
                self.logger.warning("[Agent Request Handler] No text provided for TTS")
                return False
            
            # Queue TTS request for processing
            await tts_manager._request_queue.put({
                "text": text,
                "sender_identity": sender_identity
            })
            
            self.logger.info(
                f"[Agent Request Handler] TTS request queued: '{text[:50]}...'"
            )
            
            return True
        
        except Exception as e:
            self.logger.error(
                f"[Agent Request Handler] Error handling TTS play: {e}",
                exc_info=True
            )
            return False
    
    async def handle_send_chat_message(
        self,
        payload: Dict[str, Any],
        room_context=None
    ) -> bool:
        """
        Handle send chat message request.
        
        Sends a chat message to room participants via LiveKit DataChannel.
        Uses room.local_participant.publish_data() to send message.
        
        Args:
            payload: Request payload containing message
            room_context: Room context (JobContext or similar)
        
        Returns:
            True if handled successfully
        """
        try:            
            message = payload.get("message")
            sender_name = payload.get("sender_name", "Agent")
            
            self.logger.info(
                f"[Agent Request Handler] Send chat message: "
                f"message='{message[:100] if message else ''}', "
                f"sender={sender_name}"
            )
            
            if not room_context:
                self.logger.warning(
                    "[Agent Request Handler] Room context not available, "
                    "cannot send chat message"
                )
                return False
            
            if not message:
                self.logger.warning("[Agent Request Handler] No message provided")
                return False
            
            # Prepare chat message payload (compatible with lk-chat-topic format)
            chat_payload = {
                "id": str(uuid.uuid4()),
                "timestamp": int(time.time() * 1000),
                "message": message,
                "ignoreLegacy": False
            }
            
            # Send message via DataChannel (sendText equivalent)
            await room_context.room.local_participant.publish_data(
                json.dumps(chat_payload).encode("utf-8"),
                reliable=True,
                topic="lk-chat-topic"
            )
            
            self.logger.info(
                f"[Agent Request Handler] Chat message sent successfully: '{message[:100] if message else ''}'"
            )
            return True
            
        except Exception as e:
            self.logger.error(
                f"[Agent Request Handler] Error handling send chat message: {e}",
                exc_info=True
            )
            return False


# Factory function to create handler wrappers with context
def create_request_handlers(
    control_state=None,
    event_handlers=None,
    tts_manager=None,
    room_context=None
) -> Dict[str, Any]:
    """
    Create request handler dictionary with context pre-bound.
    
    Args:
        control_state: AgentControlState instance for transcript control
        event_handlers: EventHandlers instance for transcript operations
        tts_manager: TTSManager instance for TTS playback
        room_context: Room context (JobContext) for DataChannel operations
    
    Returns:
        Dictionary mapping request_type to handler callable
    """
    handler = AgentRequestHandler()
    
    return {
        AgentRequestType.TRANSCRIPT_CONTROL.value: lambda payload: handler.handle_transcript_control(
            payload, control_state, event_handlers
        ),
        AgentRequestType.TTS_PLAY.value: lambda payload: handler.handle_tts_play(
            payload, tts_manager
        ),
        AgentRequestType.SEND_CHAT_MESSAGE.value: lambda payload: handler.handle_send_chat_message(
            payload, room_context
        ),
    }
