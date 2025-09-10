import json
import time
import asyncio
from livekit import agents

from ..logger import get_logger

logger = get_logger(__name__)


class AgentManager:
    """Manages agent identity, metadata, and status"""
    
    def __init__(self, ctx: agents.JobContext):
        self.ctx = ctx
        self.logger = get_logger("agent_manager")
        self.agent_metadata = {
            "type": "agent",
            "role": "transcription",
            "service": "vosk",
            "provider": "websocket",
            "version": "1.0.0",
            "capabilities": [
                "real_time_transcription",
                "multi_participant",
                "data_channel_communication"
            ],
            "status": "initializing",
            "created_at": int(time.time() * 1000),
            "features": {
                "transcript": True,
                "translation": True,
                "batch_processing": True,
                "reconnect_support": True
            }
        }
    
    async def setup_agent_identity(self):
        """Setup comprehensive agent identity and metadata"""
        try:
            # 1. Set agent name with descriptive format
            agent_name = "Vosk Transcription Agent"
            await self.ctx.room.local_participant.set_name(agent_name)
            self.logger.info(f"Agent name set: {agent_name}")
            
            # 2. Set comprehensive metadata
            await self.ctx.room.local_participant.set_metadata(
                json.dumps(self.agent_metadata)
            )
            self.logger.info("Agent metadata configured")
            
            # 3. Set agent attributes for quick access
            attributes = {
                "agent.type": "transcription",
                "agent.service": "vosk",
                "agent.status": "initializing",
                "agent.version": "1.0.0",
                "agent.capabilities": "transcription,translation,real_time",
                "agent.provider": "websocket",
                "agent.room_id": self.ctx.room.name or self.ctx.room.sid,
                "agent.session_start": str(int(time.time() * 1000))
            }
            
            await self.ctx.room.local_participant.set_attributes(attributes)
            self.logger.info("Agent attributes configured")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to setup agent identity: {e}")
            return False
    
    async def update_agent_status(self, status: str, additional_info: dict = None):
        """Update agent status in metadata and attributes"""
        try:
            # Update metadata
            self.agent_metadata["status"] = status
            self.agent_metadata["last_updated"] = int(time.time() * 1000)
            
            if additional_info:
                self.agent_metadata.update(additional_info)
            
            await self.ctx.room.local_participant.set_metadata(
                json.dumps(self.agent_metadata)
            )
            
            # Update attributes for quick access
            await self.ctx.room.local_participant.set_attributes({
                "agent.status": status,
                "agent.last_updated": str(int(time.time() * 1000))
            })
            
            self.logger.info(f"Agent status updated to: {status}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to update agent status: {e}")
            return False
    
    async def announce_agent_ready(self):
        """Announce agent is ready via data channel"""
        try:
            # Update status to ready first
            await self.update_agent_status("ready", {
                "ready_at": int(time.time() * 1000),
                "participants_count": len(self.ctx.room.remote_participants)
            })
            
            # Announce via data channel
            announcement = {
                "type": "agent_announcement",
                "event": "agent_ready",
                "agent": {
                    "id": self.ctx.room.local_participant.identity,
                    "name": self.ctx.room.local_participant.name,
                    "service": "vosk_transcription",
                    "capabilities": self.agent_metadata["capabilities"],
                    "status": "ready"
                },
                "room": {
                    "name": self.ctx.room.name,
                    "sid": self.ctx.room.sid,
                    "participants": len(self.ctx.room.remote_participants)
                },
                "timestamp": int(time.time() * 1000)
            }
            
            await self.ctx.room.local_participant.publish_data(
                json.dumps(announcement).encode("utf-8"),
                reliable=True,
                topic="agent_control"
            )
            
            self.logger.info("Agent ready announcement sent via data channel")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to announce agent ready: {e}")
            return False
    
    async def announce_agent_status(self, status: str, details: dict = None):
        """Announce agent status change via data channel"""
        try:
            announcement = {
                "type": "agent_status",
                "event": "status_change",
                "agent": {
                    "id": self.ctx.room.local_participant.identity,
                    "name": self.ctx.room.local_participant.name,
                    "service": "vosk_transcription",
                    "status": status
                },
                "details": details or {},
                "timestamp": int(time.time() * 1000)
            }
            
            await self.ctx.room.local_participant.publish_data(
                json.dumps(announcement).encode("utf-8"),
                reliable=True,
                topic="agent_control"
            )
            
            self.logger.debug(f"Agent status announcement sent: {status}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to announce agent status: {e}")
            return False
    
    async def handle_agent_commands(self, data_packet):
        """Handle commands sent to agent via data channel"""
        try:
            if data_packet.topic != "agent_commands":
                return False
                
            command = json.loads(data_packet.data.decode("utf-8"))
            command_type = command.get("type")
            
            self.logger.info(f"Received agent command: {command_type}")
            
            if command_type == "get_status":
                await self._handle_status_request(command)
            elif command_type == "start_transcription":
                await self._handle_start_transcription(command)
            elif command_type == "stop_transcription":
                await self._handle_stop_transcription(command)
            elif command_type == "get_capabilities":
                await self._handle_capabilities_request(command)
            else:
                self.logger.warning(f"Unknown agent command: {command_type}")
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error handling agent command: {e}")
            return False
    
    async def _handle_status_request(self, command):
        """Handle status request command"""
        response = {
            "type": "agent_response",
            "request_id": command.get("request_id"),
            "response": "status",
            "data": {
                "agent": self.agent_metadata,
                "room": {
                    "name": self.ctx.room.name,
                    "sid": self.ctx.room.sid,
                    "participants": len(self.ctx.room.remote_participants)
                },
                "timestamp": int(time.time() * 1000)
            }
        }
        
        await self.ctx.room.local_participant.publish_data(
            json.dumps(response).encode("utf-8"),
            reliable=True,
            topic="agent_responses"
        )
    
    async def _handle_capabilities_request(self, command):
        """Handle capabilities request command"""
        response = {
            "type": "agent_response",
            "request_id": command.get("request_id"),
            "response": "capabilities",
            "data": {
                "capabilities": self.agent_metadata["capabilities"],
                "features": self.agent_metadata["features"],
                "service": self.agent_metadata["service"],
                "version": self.agent_metadata["version"]
            }
        }
        
        await self.ctx.room.local_participant.publish_data(
            json.dumps(response).encode("utf-8"),
            reliable=True,
            topic="agent_responses"
        )
    
    async def _handle_start_transcription(self, command):
        """Handle start transcription command"""
        participant_id = command.get("participant_id", "all")
        
        response = {
            "type": "agent_response",
            "request_id": command.get("request_id"),
            "response": "transcription_started",
            "data": {
                "participant_id": participant_id,
                "status": "started",
                "message": f"Transcription started for {participant_id}"
            }
        }
        
        await self.ctx.room.local_participant.publish_data(
            json.dumps(response).encode("utf-8"),
            reliable=True,
            topic="agent_responses"
        )
    
    async def _handle_stop_transcription(self, command):
        """Handle stop transcription command"""
        participant_id = command.get("participant_id", "all")
        
        response = {
            "type": "agent_response",
            "request_id": command.get("request_id"),
            "response": "transcription_stopped",
            "data": {
                "participant_id": participant_id,
                "status": "stopped",
                "message": f"Transcription stopped for {participant_id}"
            }
        }
        
        await self.ctx.room.local_participant.publish_data(
            json.dumps(response).encode("utf-8"),
            reliable=True,
            topic="agent_responses"
        )
    
    def get_agent_info(self) -> dict:
        """Get current agent information"""
        return {
            "metadata": self.agent_metadata,
            "participant": {
                "identity": self.ctx.room.local_participant.identity,
                "name": self.ctx.room.local_participant.name,
                "sid": self.ctx.room.local_participant.sid
            },
            "room": {
                "name": self.ctx.room.name,
                "sid": self.ctx.room.sid
            }
        }
    
    async def cleanup(self):
        """Cleanup agent resources and announce shutdown"""
        try:
            await self.update_agent_status("shutting_down")
            
            shutdown_announcement = {
                "type": "agent_announcement",
                "event": "agent_shutdown",
                "agent": {
                    "id": self.ctx.room.local_participant.identity,
                    "name": self.ctx.room.local_participant.name,
                    "service": "vosk_transcription"
                },
                "timestamp": int(time.time() * 1000)
            }
            
            await self.ctx.room.local_participant.publish_data(
                json.dumps(shutdown_announcement).encode("utf-8"),
                reliable=True,
                topic="agent_control"
            )
            
            self.logger.info("Agent cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during agent cleanup: {e}")