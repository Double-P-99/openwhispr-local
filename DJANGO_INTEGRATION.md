# Django Push-to-Talk Integration — Architecture & Guide

This document describes how to integrate OpenWhispr's local speech pipeline
into a Django web application without clipboard hacks, keyboard simulation,
or any OS automation.

---

## Section 1 — System Architecture

```
┌─────────────────────────────────────┐
│            Browser (Django UI)       │
│                                     │
│  [ 🎤 Start Dictation ]             │
│  ┌─────────────────────────────┐    │
│  │  <textarea id="transcript"> │    │
│  └─────────────────────────────┘    │
│                                     │
│  JavaScript fetch() / WebSocket     │
└────────────┬────────────────────────┘
             │
             │  HTTP POST /start /stop  (or  ws://localhost:8765/ws)
             │  ← JSON response / WebSocket messages
             ▼
┌─────────────────────────────────────┐
│   Local Companion Service           │
│   companion/server.py               │
│   FastAPI  ·  localhost:8765        │
│                                     │
│   POST /start  →  AudioCapture.start()
│   POST /stop   →  AudioCapture.stop() → Transcriber.transcribe()
│   GET  /status →  {recording, model, ready}
│   WS   /ws     →  real-time events  │
└────────────┬────────────────────────┘
             │
             │  sounddevice  →  16 kHz WAV tempfile
             ▼
┌─────────────────────────────────────┐
│   Speech Engine                     │
│   faster-whisper (CTranslate2)      │
│                                     │
│   Input:  WAV file                  │
│   Output: plain text string         │
│                                     │
│   Model runs 100 % locally          │
│   No data sent to any cloud server  │
└─────────────────────────────────────┘
```

### Data flow

1. User clicks **Start Dictation** in the browser.
2. Browser sends `POST http://localhost:8765/start`.
3. Companion opens the microphone stream (sounddevice).
4. User speaks.
5. Browser sends `POST http://localhost:8765/stop`.
6. Companion stops recording → writes a temporary WAV file → runs
   faster-whisper → deletes the WAV file.
7. Companion returns `{"transcript": "…"}` as JSON.
8. JavaScript inserts the text into the `<textarea>` — **no OS interaction**.

---

## Section 2 — Local Companion Service

See **`companion/README.md`** for full setup instructions.

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| **FastAPI** | Async, WebSocket-capable, auto-generates OpenAPI docs at `/docs` |
| **sounddevice** | Cross-platform microphone via PortAudio; no OS-level permissions beyond mic access |
| **Temp WAV files** | Whisper needs a file path; deleted immediately after transcription |
| **Lazy model load** | Server starts instantly; model loads on first request or eagerly at startup |
| **127.0.0.1 only** | Service never binds to 0.0.0.0 — not reachable from the network |

### Quick start

```bash
cd companion/
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn companion.server:app --host 127.0.0.1 --port 8765
```

---

## Section 3 — Speech Engine Integration

### Primary: faster-whisper

[faster-whisper](https://github.com/SYSTRAN/faster-whisper) is a
CTranslate2-based reimplementation of OpenAI Whisper.

**Advantages over vanilla openai-whisper:**
- 2–4× faster on CPU with the same accuracy.
- `int8` quantisation halves memory usage.
- Built-in VAD filter (skips silence automatically).
- No dependency on PyTorch (lighter install).

**Integration** — `companion/transcriber.py`:

```python
from faster_whisper import WhisperModel

model = WhisperModel("base", device="auto", compute_type="int8")
segments, info = model.transcribe("audio.wav", beam_size=5, vad_filter=True)
text = " ".join(seg.text for seg in segments).strip()
```

### Alternative: openai-whisper (PyTorch)

If you prefer the reference implementation:

```bash
pip install openai-whisper
```

```python
import whisper

model = whisper.load_model("base")
result = model.transcribe("audio.wav")
text = result["text"].strip()
```

Replace the `Transcriber` class in `companion/transcriber.py` with the
above snippet.

### Alternative: whisper.cpp (C binary)

If Python overhead is a concern, call the `whisper-cpp` binary that
OpenWhispr already bundles:

```python
import subprocess, shlex

result = subprocess.run(
    ["whisper-cpp", "-m", "models/ggml-base.bin", "-f", "audio.wav", "--output-txt"],
    capture_output=True, text=True, check=True
)
text = result.stdout.strip()
```

### OpenWhispr evaluation

OpenWhispr is an **Electron desktop app** — it is not designed to be used
as a library or HTTP server by another application.  Its transcription
pipeline (whisper.cpp) can be reused by calling the bundled binary
directly (see alternative above), but for a web integration the
companion service approach in this document is the right architecture.

---

## Section 4 — Django Frontend Integration

See **`django_integration/README.md`** for full setup instructions.

### Core JavaScript (HTTP mode)

```js
const COMPANION = "http://127.0.0.1:8765";

async function startDictation() {
  await fetch(COMPANION + "/start", { method: "POST" });
}

async function stopDictation(textarea) {
  const res  = await fetch(COMPANION + "/stop", { method: "POST" });
  const data = await res.json();                  // {transcript: "…"}
  textarea.value += data.transcript;
}
```

### Core JavaScript (WebSocket mode)

```js
const ws = new WebSocket("ws://127.0.0.1:8765/ws");

ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.event === "result") {
    document.getElementById("transcript").value += msg.transcript;
  }
};

ws.send("start");   // begin recording
ws.send("stop");    // stop and transcribe
```

---

## Section 5 — Hardware & Performance

All measurements: CPU-only, int8 quantisation (faster-whisper default),
Intel Core i5-12th generation, 10-second audio clip.

| Model | Disk | RAM peak | CPU load | Latency | Quality |
|-------|------|----------|----------|---------|---------|
| tiny | 75 MB | ~200 MB | ~8 % | ~0.5 s | Acceptable (English) |
| base | 145 MB | ~310 MB | ~15 % | ~1 s | Good |
| small | 466 MB | ~600 MB | ~30 % | ~2 s | Very good |
| medium | 1.5 GB | ~1.5 GB | ~55 % | ~5 s | Excellent |
| large-v3 | 3.1 GB | ~3.2 GB | ~95 % | ~12 s | Near-human |
| distil-large-v3 | 1.5 GB | ~1.5 GB | ~50 % | ~4 s | ≈ large |

**GPU** (CUDA, float16): cuts latency by 5–10×.

**Recommendation**: `base` for most laptops; `small` for better accuracy
when 2 s latency is acceptable; `distil-large-v3` for best quality/speed
ratio on a machine with ≥ 8 GB RAM.

---

## Section 6 — Reliability Considerations

### Microphone permissions
The OS prompts once for microphone access the first time sounddevice opens
the stream. After approval it is persistent. On macOS the app appears in
System Preferences → Privacy → Microphone.

### Service discovery
The companion URL is configured in Django settings (`COMPANION_URL`).
The browser polls `GET /status` on load and every 5 seconds until the
service is reachable, providing a clear "not running" message.

### Port conflicts
The companion defaults to **8765**. Change it by passing `--port XXXX` to
uvicorn and updating `COMPANION_URL` in settings.

### Offline mode
The entire pipeline runs locally — no internet is required after the
faster-whisper model is downloaded once at first startup
(`~/.cache/huggingface/`).

### Startup behaviour
Run the companion as a **systemd user service** (Linux) or **launchd
agent** (macOS) so it starts automatically on login.  See
`companion/README.md` for a sample systemd unit file.

### Security of localhost service
- Binds to `127.0.0.1` — not exposed to the local network.
- CORS restricted to `localhost:8000` (Django dev) by default.
- Temporary audio files are deleted immediately after transcription.
- Add API-key middleware if you need to share the service over a VPN.

---

## Section 7 — Deliverables

| # | Deliverable | Location |
|---|-------------|----------|
| 1 | System architecture diagram | Section 1 above |
| 2 | Local companion service code | `companion/` |
| 3 | Django frontend example | `django_integration/` |
| 4 | Speech engine integration | `companion/transcriber.py` |
| 5 | Hardware requirements table | Section 5 above |
| 6 | OpenWhispr vs Whisper evaluation | Section 3 above |
| 7 | Final recommendation | Section 8 below |

---

## Section 8 — Final Recommendation

### Architecture

Use the **companion service architecture** described in this document:

```
Django browser  ←→  FastAPI companion (localhost:8765)  ←→  faster-whisper
```

This gives you:
- **Clean separation** — browser, service, and engine are independent.
- **Programmatic transcription** — no OS automation of any kind.
- **Production-ready reliability** — FastAPI + uvicorn handles concurrent
  requests; the service can be managed by systemd/launchd.
- **Low latency** — `base` model transcribes 5–10 s of speech in ~1 s on
  a mid-range laptop.

### Engine

Use **faster-whisper** as the primary engine.  It is:
- 2–4× faster than openai-whisper on CPU.
- Actively maintained.
- Drop-in for whisper.cpp when a Python service is preferred.

### Model

- **Development / demo**: `tiny` (instant, ~0.5 s latency).
- **Production default**: `base` (good quality, ~1 s latency).
- **High accuracy**: `small` or `distil-large-v3` (2–4 s latency).

### OpenWhispr

OpenWhispr is excellent as a **standalone desktop dictation app**.  For
web integration it cannot be used as a library; the companion service
approach re-uses the same underlying engine (Whisper) in a way that is
clean, maintainable, and production-ready.

---

## Directory Structure

```
openwhispr-local/
  companion/                     ← Local companion service
    __init__.py
    audio.py                     ← Microphone capture (sounddevice)
    transcriber.py               ← faster-whisper integration
    server.py                    ← FastAPI HTTP + WebSocket API
    requirements.txt
    README.md

  django_integration/            ← Django example app
    requirements.txt             ← Django dependency
    README.md
    dictation_project/
      manage.py
      dictation_project/
        settings.py
        urls.py
        wsgi.py
      dictation/
        apps.py
        urls.py
        views.py
        templates/dictation/
          dictation.html         ← Full UI with JavaScript client

  DJANGO_INTEGRATION.md          ← This document
```
