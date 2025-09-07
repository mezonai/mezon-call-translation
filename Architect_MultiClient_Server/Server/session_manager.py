import logging

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self):
        self.sessions = {}  # session_id -> {clients, transcripts}

    def add_client(self, session_id, client_id, websocket, transcript, translation, language):
        if session_id not in self.sessions:
            self.sessions[session_id] = {"clients": {}, "transcripts": {}, "translation": {}}
        self.sessions[session_id]["clients"][client_id] = {
            "websocket": websocket,
            "transcripts": transcript,
            "translation": translation,
            "language" : language
        }
        logger.info("Added client %s to session %s (transcript=%s, translation=%s, language=%s)", client_id, session_id, transcript, translation, language)

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


    # def update_transcript(self, session_id, client_id, text):
    #     if session_id in self.sessions:
    #         self.sessions[session_id]["transcripts"][client_id] = text

    # def get_transcript_json(self, session_id):
    #     if session_id in self.sessions:
    #         return {
    #             "session_id": session_id,
    #             "transcripts": self.sessions[session_id]["transcripts"]
    #         }
    #     return {}

    def get_clients_to_notify_transcript(self, session_id):
        if session_id in self.sessions:
            sockets = [
                info["websocket"]
                for info in self.sessions[session_id]["clients"].values()
                if info.get("transcripts", False)
            ]
            logger.debug("Found %s transcript clients for session %s", len(sockets), session_id)
            return sockets
        return []

    def get_clients_to_notify_translation(self, session_id):
        if session_id in self.sessions:
            sockets = [
                info["websocket"]
                for info in self.sessions[session_id]["clients"].values()
                if info.get("translation", False)
            ]
            logger.debug("Found %s translation clients for session %s", len(sockets), session_id)
            return sockets
        return []

session_manager = SessionManager()

# session_manager.py




