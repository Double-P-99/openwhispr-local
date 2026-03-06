# OpenWhispr Local Companion Service

A lightweight Python HTTP + WebSocket service that runs on **localhost:8765** and
provides programmatic push-to-talk speech dictation to any web frontend
(Django, React, plain HTML, …).

No clipboard tricks. No keyboard simulation. No OS automation.
The transcript is returned as plain JSON.

---

## Architecture

```
Browser (Django page)
    │
    │  HTTP  POST /start  →  start recording
    │  HTTP  POST /stop   →  stop, transcribe, return text
    │  (or WebSocket /ws for real-time events)
    ▼
companion/server.py   (FastAPI, localhost:8765)
    │
    │  sounddevice  →  16 kHz WAV tempfile
    ▼
transcriber.py        (faster-whisper)
    │
    │  returns raw text string
    ▼
Browser inserts text into <textarea>
```

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.10 + |
| faster-whisper | 1.0 + |
| sounddevice | 0.5 + |
| FastAPI + uvicorn | 0.115 + / 0.30 + |

On **Linux** you may also need:

```bash
sudo apt install portaudio19-dev libsndfile1
```

On **macOS**:

```bash
brew install portaudio
```

On **Windows**: portaudio ships inside the `sounddevice` wheel — no extra step needed.

---

## Installation

```bash
cd companion/
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Running the service

```bash
# Default: base model, auto device, port 8765
uvicorn companion.server:app --host 127.0.0.1 --port 8765

# Use a different Whisper model
WHISPER_MODEL=small uvicorn companion.server:app --host 127.0.0.1 --port 8765

# GPU inference (requires CUDA)
WHISPER_MODEL=medium WHISPER_DEVICE=cuda WHISPER_COMPUTE_TYPE=float16 \
    uvicorn companion.server:app --host 127.0.0.1 --port 8765
```

The first startup downloads the model from HuggingFace (~150 MB for `base`).
Subsequent starts load from the local cache in `~/.cache/huggingface/`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `WHISPER_MODEL` | `base` | faster-whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`, …) |
| `WHISPER_DEVICE` | `auto` | `auto`, `cpu`, `cuda` |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8`, `float16`, `float32` |
| `COMPANION_ALLOWED_ORIGINS` | *(empty)* | Space-separated extra CORS origins |

---

## HTTP API

### `POST /start`

Start microphone capture.

**Response 200**
```json
{"status": "recording", "message": "Microphone capture started"}
```

**Response 409** — already recording
```json
{"detail": "A recording session is already active.  POST /stop first."}
```

---

### `POST /stop`

Stop capture, transcribe, and return the text.

**Response 200**
```json
{
  "status": "done",
  "transcript": "Hello, this is a test of the dictation system.",
  "duration_seconds": 3.84
}
```

**Response 409** — no active session
```json
{"detail": "No active recording session.  POST /start first."}
```

---

### `GET /status`

```json
{"recording": false, "model": "base", "ready": true}
```

---

## WebSocket API  (`ws://localhost:8765/ws`)

Send plain-text commands; receive JSON events.

| Send | Description |
|---|---|
| `start` | Begin recording |
| `stop`  | Stop and transcribe |

| Received event | Payload |
|---|---|
| `recording_started` | `{}` |
| `transcribing` | `{}` |
| `result` | `{"transcript": "…", "duration_seconds": 1.23}` |
| `error` | `{"message": "…"}` |

**Example (browser)**
```js
const ws = new WebSocket('ws://localhost:8765/ws');

ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.event === 'result') {
    document.getElementById('transcript').value += msg.transcript + ' ';
  }
};

ws.send('start');
// user speaks …
ws.send('stop');
```

---

## Hardware requirements table

| Model | Disk | RAM (peak) | CPU (i5-12th) | Latency (10 s audio) | Quality |
|---|---|---|---|---|---|
| tiny | 75 MB | ~200 MB | ~8 % | ~0.5 s | Acceptable (en) |
| base | 145 MB | ~310 MB | ~15 % | ~1 s | Good (en) |
| small | 466 MB | ~600 MB | ~30 % | ~2 s | Very good |
| medium | 1.5 GB | ~1.5 GB | ~55 % | ~5 s | Excellent |
| large-v3 | 3.1 GB | ~3.2 GB | ~95 % | ~12 s | Near-human |
| distil-large-v3 | 1.5 GB | ~1.5 GB | ~50 % | ~4 s | Near-large |

*All numbers measured CPU-only (int8). GPU (CUDA) cuts latency by 5–10×.*

**Recommendation**: `base` for real-time feel on typical laptops; `small` for
better accuracy when 2 s latency is acceptable.

---

## Running as a system service (Linux systemd example)

```ini
# ~/.config/systemd/user/openwhispr-companion.service
[Unit]
Description=OpenWhispr Local Companion
After=network.target

[Service]
WorkingDirectory=%h/openwhispr
ExecStart=%h/openwhispr/companion/.venv/bin/uvicorn companion.server:app \
    --host 127.0.0.1 --port 8765
Restart=on-failure
Environment=WHISPER_MODEL=base

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now openwhispr-companion
```

---

## Security

* The service binds to **127.0.0.1 only** — not reachable from other machines.
* CORS is restricted to `localhost:8000` (Django dev server) by default.
  Add your production origin via `COMPANION_ALLOWED_ORIGINS`.
* No authentication is required for localhost-only deployments; add an
  API-key header middleware if you expose this over a VPN or tunnel.
* Temporary WAV files are deleted immediately after transcription.
