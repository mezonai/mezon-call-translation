# Mezon Call Dashboard

A modern React dashboard for managing and viewing meeting-room data.

## 🚀 Quick Start

### 1. Install

```bash
cd Architect_MultiClient_Server/dashboard
npm install
```

### 2. Configure

Copy `.env.example` to `.env` and adjust if needed (the default already points at `orchestrator_service` running locally on port 8002):

```env
VITE_API_BASE_URL=http://localhost:8002
VITE_AUDIO_BASE_URL=http://localhost:8002/recordings

# Login via Mezon OAuth2 — these 3 variables are required
VITE_MEZON_AUTH_URL=https://your-oauth2-domain/oauth2/auth
VITE_MEZON_CLIENT_ID=your_client_id_here
VITE_MEZON_REDIRECT_URI=http://localhost:3000/callback
```

`VITE_API_BASE_URL` has no hardcoded default matching `.env.example` — if left unset, `src/services/api.js` falls back to `http://localhost:8000` (the old port, not the current orchestrator port), so always set this variable explicitly. When running via `npm run dev`, `vite.config.js` also proxies `/api` to a different default orchestrator (`http://172.16.110.19:8002`) — edit `server.proxy` in that file if your backend is at a different address; `VITE_API_BASE_URL` has no effect on this proxy.

### 3. Run

```bash
npm run dev
```

The dashboard runs at: **http://localhost:3000**

## ✨ Features

### 🔐 Login
- Login via **Mezon OAuth2** (`src/components/Login.jsx`, `Callback.jsx`, `src/contexts/AuthContext.jsx`) — no more static API key.
- The JWT obtained after login is automatically attached to every request to the backend (`src/services/api.js`).

### 📋 Room List
- Displays all meeting rooms with pagination
- Search rooms by name
- Shows status and track count
- Real-time data refresh

### 📊 Room Detail

**Overview tab:**
- Basic room information
- Stats: tracks, duration, segments

**Participants & Transcripts tab:**
- Participant list with track info
- **Load full transcript** per participant
- Displays segments with timestamp and confidence score
- Scrollable transcript viewer

**Summary tab:**
- Meeting key points
- Action items
- Decisions
- Participant list

## 🛠 Tech Stack

- **React 18** - UI library
- **Vite** - Build tool
- **React Router** - Routing
- **Axios** - HTTP client
- **Tailwind CSS** - Styling

## 📖 Documentation

- **[SETUP.md](./SETUP.md)** - Detailed setup guide
- **[OVERVIEW.md](./OVERVIEW.md)** - Project overview

## 🎯 Build Production

```bash
npm run build    # Build
npm run preview  # Preview build
```

## 🔧 Troubleshooting

### CORS error
Add CORS middleware to the backend (orchestrator service, see `Architect_MultiClient_Server/orchestrator_service/main.py`):

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Can't connect to backend
1. Check that `orchestrator_service` is running on port 8002 (not 8000 — see the note in the Configure section).
2. Check that `.env` has the correct `VITE_API_BASE_URL`/`VITE_AUDIO_BASE_URL`, and `vite.config.js`'s `server.proxy` if running via `npm run dev`.
3. Check that Mezon OAuth2 login succeeded (an expired token or misconfigured `VITE_MEZON_*` will make every API request return 401).

## 📸 Screenshots

### Room List
- Table view with pagination
- Status badges
- Search functionality

### Room Detail
- Multi-tab interface
- Statistics cards
- Interactive transcript viewer
- Summary display

## 🤝 Contributing

See [OVERVIEW.md](./OVERVIEW.md) to understand the project structure and how to add new features.

---

**Version**: 1.0.0
**License**: Follows the main project's license
