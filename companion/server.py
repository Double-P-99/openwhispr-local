"""
server.py — OpenWhispr Local Companion Service.

Runs on http://localhost:8765 and exposes a simple HTTP + WebSocket API
that a Django (or any web) frontend can call to perform push-to-talk
speech dictation without any OS clipboard tricks or keyboard simulation.

Endpoints
---------
POST /start
    Start microphone capture.  Returns {"status": "recording"}.

POST /stop
    Stop capture, transcribe, return text.
    Returns {"status": "done", "transcript": "…"}.

GET  /status
    Current service state.
    Returns {"recording": bool, "model": str, "ready": bool}.

WebSocket /ws
    Real-time interface — send "start" / "stop" text frames, receive
    JSON messages as transcription progresses.

Usage
-----
    uvicorn companion.server:app --host 127.0.0.1 --port 8765

Or simply:
    python -m companion.server
"""

from __future__ import annotations

import asyncio
import logging
import os
import wave
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .audio import AudioCapture
from .transcriber import Transcriber

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state — one capture session at a time
# ---------------------------------------------------------------------------

_capture = AudioCapture()
_transcriber = Transcriber()

# ---------------------------------------------------------------------------
# CORS — allow the Django dev server and any localhost origin.
# In production, restrict `allow_origins` to your domain.
# ---------------------------------------------------------------------------

_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# Allow extra origins via environment variable (space-separated).
_extra = os.environ.get("COMPANION_ALLOWED_ORIGINS", "")
if _extra:
    _ALLOWED_ORIGINS.extend(_extra.split())

# ---------------------------------------------------------------------------
# App lifecycle — warm up the model before accepting requests
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("OpenWhispr companion service starting …")
    loop = asyncio.get_event_loop()
    # Load model in a thread so we don't block the event loop
    await loop.run_in_executor(None, _transcriber.load)
    logger.info("Companion service ready on http://127.0.0.1:8765")
    yield
    logger.info("Companion service shutting down.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OpenWhispr Companion",
    description="Local speech-to-text service for Django web integration",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class StartResponse(BaseModel):
    status: str = "recording"
    message: str = "Microphone capture started"


class StopResponse(BaseModel):
    status: str
    transcript: str
    duration_seconds: float


class StatusResponse(BaseModel):
    recording: bool
    model: str
    ready: bool


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@app.post("/start", response_model=StartResponse, summary="Start microphone capture")
async def start_recording():
    """
    Begin recording from the default microphone.

    Returns 409 if a recording session is already active.
    """
    if _capture.is_recording:
        raise HTTPException(
            status_code=409,
            detail="A recording session is already active.  POST /stop first.",
        )
    try:
        _capture.start()
    except Exception as exc:
        logger.exception("Failed to start capture")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return StartResponse()


@app.post("/stop", response_model=StopResponse, summary="Stop capture and transcribe")
async def stop_recording():
    """
    Stop the active recording, transcribe the audio, and return the text.

    The temporary WAV file is automatically deleted after transcription.
    """
    if not _capture.is_recording:
        raise HTTPException(
            status_code=409,
            detail="No active recording session.  POST /start first.",
        )

    loop = asyncio.get_event_loop()

    # Stop capture (blocking I/O — run in thread)
    try:
        wav_path: Path = await loop.run_in_executor(None, _capture.stop)
    except Exception as exc:
        logger.exception("Failed to stop capture")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Compute duration before transcription
    try:
        with wave.open(str(wav_path), "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
    except Exception:
        duration = 0.0

    # Transcribe (blocking — run in thread)
    try:
        transcript: str = await loop.run_in_executor(
            None, _transcriber.transcribe, wav_path
        )
    except Exception as exc:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        # Always clean up the temporary file
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass

    return StopResponse(
        status="done",
        transcript=transcript,
        duration_seconds=round(duration, 2),
    )


@app.get("/status", response_model=StatusResponse, summary="Service status")
async def status():
    """Return the current recording state and model information."""
    return StatusResponse(
        recording=_capture.is_recording,
        model=_transcriber.model_name,
        ready=True,
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint — real-time control and result streaming
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket interface for real-time dictation control.

    Client sends text commands:
        "start"  →  begin recording
        "stop"   →  stop recording, transcribe, receive result

    Server sends JSON messages:
        {"event": "recording_started"}
        {"event": "transcribing"}
        {"event": "result", "transcript": "…", "duration_seconds": 1.23}
        {"event": "error", "message": "…"}
    """
    await websocket.accept()
    logger.info("WebSocket client connected: %s", websocket.client)

    try:
        while True:
            data = await websocket.receive_text()
            command = data.strip().lower()

            if command == "start":
                if _capture.is_recording:
                    await websocket.send_json(
                        {"event": "error", "message": "Already recording"}
                    )
                    continue
                try:
                    _capture.start()
                    await websocket.send_json({"event": "recording_started"})
                except Exception as exc:
                    await websocket.send_json({"event": "error", "message": str(exc)})

            elif command == "stop":
                if not _capture.is_recording:
                    await websocket.send_json(
                        {"event": "error", "message": "Not recording"}
                    )
                    continue

                await websocket.send_json({"event": "transcribing"})

                loop = asyncio.get_event_loop()
                try:
                    wav_path = await loop.run_in_executor(None, _capture.stop)
                except Exception as exc:
                    await websocket.send_json({"event": "error", "message": str(exc)})
                    continue

                try:
                    with wave.open(str(wav_path), "rb") as wf:
                        duration = wf.getnframes() / wf.getframerate()
                except Exception:
                    duration = 0.0

                try:
                    transcript = await loop.run_in_executor(
                        None, _transcriber.transcribe, wav_path
                    )
                    await websocket.send_json(
                        {
                            "event": "result",
                            "transcript": transcript,
                            "duration_seconds": round(duration, 2),
                        }
                    )
                except Exception as exc:
                    await websocket.send_json({"event": "error", "message": str(exc)})
                finally:
                    try:
                        wav_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            else:
                await websocket.send_json(
                    {
                        "event": "error",
                        "message": f"Unknown command: {data!r}.  Send 'start' or 'stop'.",
                    }
                )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "companion.server:app",
        host="127.0.0.1",
        port=8765,
        log_level="info",
        reload=False,
    )
