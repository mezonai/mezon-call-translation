
# STT Agent for LiveKit

**Speech-to-Text (STT) Agent** using:

- [Vosk](https://alphacephei.com/vosk/)  
- [WhisperLive (Faster-Whisper)](https://github.com/SYSTRAN/faster-whisper)  
- [LiveKit Server](https://github.com/livekit/livekit)  
- [LiveKit Meet](https://github.com/livekit-examples/meet)  

---

## Setup

### 1. LiveKit Server

Pull the Docker image:

```bash
docker pull livekit/livekit-server:v1.9.0
````

Run the server in dev mode:

```bash
docker run --rm -it \
  -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  livekit/livekit-server:v1.9.0 --dev --bind 0.0.0.0
```

> **Note:** Dev mode automatically generates credentials:
> API Key: `devkey`
> API Secret: `secret`

---

### 2. LiveKit Meet (Frontend)

Clone the repo and install dependencies:

```bash
git clone https://github.com/livekit-examples/meet
cd meet
pnpm install
```

Setup environment variables:

```bash
cp .env.example .env.local
```

Update `.env.local` with your API Key/Secret.

Start the dev server:

```bash
pnpm dev
```

Visit [http://localhost:3000](http://localhost:3000) 🎉

---

### 3. WhisperLive STT

#### GPU Version

```bash
docker run -it --gpus all -p 9090:9090 ghcr.io/collabora/whisperlive-gpu:latest
```

#### CPU Version

```bash
docker run -it -p 9090:9090 ghcr.io/collabora/whisperlive-cpu:latest
```

Run the STT agent:

```bash
python Architect_MultiClient_Server/Server/agents/Whisperlive_agent.py
```

---

### 4. Vosk STT

Start the server:

```bash
python Architect_MultiClient_Server/Server/main.py
```

Accessible at [http://localhost:8001](http://localhost:8001)

Run the agent:

```bash
python Architect_MultiClient_Server/Server/agents/Vosk_agent.py
```

---

This setup enables **real-time speech-to-text** for LiveKit meetings using **WhisperLive** or **Vosk**.

```

