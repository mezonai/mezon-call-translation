import logging

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self):
        self.sessions = {}  # session_id -> {clients, transcripts}

    def add_client(self, session_id, client_id, websocket, transcript, language):
        if session_id not in self.sessions:
            self.sessions[session_id] = {"clients": {}, "transcripts": {}}
        self.sessions[session_id]["clients"][client_id] = {
            "websocket": websocket,
            "transcripts": transcript,
            "language" : language,
            "last_text": "", 
        }
        logger.info("Added client %s to session %s (transcript=%s, language=%s)", client_id, session_id, transcript, language)

    def remove_client(self, session_id, client_id):
        if session_id in self.sessions:
            self.sessions[session_id]["clients"].pop(client_id, None)
            self.sessions[session_id]["transcripts"].pop(client_id, None)
            if not self.sessions[session_id]["clients"]:
                self.sessions.pop(session_id)
                logger.info("Removed last client; session %s closed", session_id)
            else:
                logger.info("Removed client %s from session %s", client_id, session_id)
    
    def get_client_info(self, session_id, client_id):
        """Return the full client info dict (or None if not found)."""
        return self.sessions.get(session_id, {}).get("clients", {}).get(client_id)

    def get_client_language(self, session_id, client_id):
        """Return the client's language (or None if not set)."""
        client_info = self.get_client_info(session_id, client_id)
        return client_info.get("language") if client_info else None

    def get_clients_to_notify_transcript(self, session_id, sender_client_id=None):
        """
        Get clients to notify for transcript.
        If sender_client_id is provided, only return that specific client if they want transcripts.
        Otherwise, return all clients in the session who want transcripts (original behavior).
        """
        if session_id not in self.sessions:
            return []
        
        clients = self.sessions[session_id]["clients"]
        
        # If sender_client_id is specified, only return that client
        if sender_client_id:
            client_info = clients.get(sender_client_id)
            if client_info and client_info.get("transcripts", False):
                logger.debug("Returning transcript only to sender client %s for session %s", sender_client_id, session_id)
                return [client_info["websocket"]]
            else:
                logger.debug("Sender client %s not found or doesn't want transcripts for session %s", sender_client_id, session_id)
                return []
        
        # Original behavior: return all clients who want transcripts
        sockets = [
            info["websocket"]
            for info in clients.values()
            if info.get("transcripts", False)
        ]
        logger.debug("Found %s transcript clients for session %s", len(sockets), session_id)
        return sockets



session_manager = SessionManager()