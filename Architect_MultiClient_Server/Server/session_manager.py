class SessionManager:
    def __init__(self):
        self.sessions = {}  # session_id -> {clients, transcripts}

    def add_client(self, session_id, client_id, websocket, transcript):
        if session_id not in self.sessions:
            self.sessions[session_id] = {"clients": {}, "transcripts": {}}
        self.sessions[session_id]["clients"][client_id] = {
            "websocket": websocket,
            "transcript": transcript
        }

    def remove_client(self, session_id, client_id):
        if session_id in self.sessions:
            self.sessions[session_id]["clients"].pop(client_id, None)
            self.sessions[session_id]["transcripts"].pop(client_id, None)
            if not self.sessions[session_id]["clients"]:
                self.sessions.pop(session_id)

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

    def get_clients_to_notify(self, session_id):
        if session_id in self.sessions:
            return [
                info["websocket"]
                for info in self.sessions[session_id]["clients"].values()
                if info["transcript"]
            ]
        return []
    
    def get_client_websocket(self, session_id, client_id):
        """Hàm mới: Lấy websocket của một client cụ thể."""
        if session_id in self.sessions:
            client_info = self.sessions[session_id]["clients"].get(client_id)
            if client_info:
                return client_info["websocket"]
        return None

session_manager = SessionManager()

# session_manager.py




