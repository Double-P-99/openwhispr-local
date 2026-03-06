#!/usr/bin/env python3
"""
whisper_bridge.py — Local WebSocket bridge for OpenWhispr Django integration.

Architecture (Path 2):
    Browser (WebSocket client)
        │ ws://127.0.0.1:9876
        ▼
    whisper_bridge.py  (this script)
        │ HTTP POST multipart
        ▼
    whisper-server (localhost:8178–8199)
        │ JSON {"text": "..."}
        ▼
    whisper_bridge.py  → browser

Protocol:
    Control frames  (JSON text):
        {"action": "start", "language": "en", "prompt": ""}  → start buffering audio
        {"action": "stop"}                                     → finalise, transcribe, return result
        {"action": "ping"}                                     → {"status": "pong"}

    Audio frames (binary): raw audio bytes (webm/ogg/wav/mp4 — anything FFmpeg handles)

    Result frames (JSON text):
        {"status": "ok", "text": "transcription"}
        {"status": "error", "message": "description"}

Usage:
    python whisper_bridge.py [options]

    Options:
        --port         Bridge WebSocket port (default: 9876)
        --whisper-url  Base URL of whisper-server (default: auto-discover 8178-8199)
        --max-bytes    Maximum audio size in bytes (default: 10485760 = 10 MB)
        --language     Default language (default: auto)
        -v, --verbose  Verbose logging
"""

import argparse
import asyncio
import io
import json
import logging
import sys
from typing import Optional

import aiohttp
import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger("whisper_bridge")

# ─── Configuration defaults ──────────────────────────────────────────────────
DEFAULT_PORT = 9876
WHISPER_PORT_RANGE = range(8178, 8200)  # inclusive 8178–8199 (range end is exclusive)
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
TRANSCRIPTION_TIMEOUT = 120  # seconds


# ─── whisper-server discovery ─────────────────────────────────────────────────
async def discover_whisper_server(preferred_url: Optional[str] = None) -> Optional[str]:
    """
    Find a running whisper-server. If preferred_url is given, check it first.
    Otherwise scan the port range 8178–8199.
    """
    async with aiohttp.ClientSession() as session:
        if preferred_url:
            try:
                async with session.get(f"{preferred_url}/health", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    if r.status == 200:
                        logger.info("whisper-server found at %s", preferred_url)
                        return preferred_url
            except Exception:
                logger.warning("Preferred URL %s not reachable, scanning…", preferred_url)

        for port in WHISPER_PORT_RANGE:
            url = f"http://127.0.0.1:{port}"
            try:
                async with session.get(f"{url}/health", timeout=aiohttp.ClientTimeout(total=0.5)) as r:
                    if r.status == 200:
                        logger.info("whisper-server discovered at %s", url)
                        return url
            except Exception:
                continue

    return None


# ─── Transcription ────────────────────────────────────────────────────────────
async def transcribe(
    whisper_base_url: str,
    audio_bytes: bytes,
    language: str = "auto",
    prompt: str = "",
) -> str:
    """POST audio to the whisper-server /inference endpoint and return text."""
    url = f"{whisper_base_url}/inference"

    data = aiohttp.FormData()
    data.add_field(
        "file",
        io.BytesIO(audio_bytes),
        filename="audio.webm",
        content_type="audio/webm",
    )
    data.add_field("response_format", "json")
    if language and language != "auto":
        data.add_field("language", language)
    if prompt:
        data.add_field("prompt", prompt)

    timeout = aiohttp.ClientTimeout(total=TRANSCRIPTION_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=data) as resp:
            resp.raise_for_status()
            result = await resp.json()
            return result.get("text", "").strip()


# ─── WebSocket handler ────────────────────────────────────────────────────────
class Session:
    """Per-connection state."""

    def __init__(self):
        self.audio_chunks: list[bytes] = []
        self.language: str = "auto"
        self.prompt: str = ""
        self.recording: bool = False


async def handle_connection(
    websocket: WebSocketServerProtocol,
    whisper_url_ref: list,  # mutable reference so it can be updated
    max_bytes: int,
):
    """Handle one WebSocket client connection."""
    remote = websocket.remote_address
    logger.debug("Client connected: %s", remote)
    session = Session()

    try:
        async for message in websocket:
            # ── Binary frame = audio chunk ────────────────────────────────────
            if isinstance(message, bytes):
                if session.recording:
                    session.audio_chunks.append(message)
                continue

            # ── Text frame = control message ─────────────────────────────────
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"status": "error", "message": "Invalid JSON."}))
                continue

            action = msg.get("action")

            if action == "ping":
                await websocket.send(json.dumps({"status": "pong"}))

            elif action == "start":
                session.audio_chunks = []
                session.language = msg.get("language", "auto")
                session.prompt = msg.get("prompt", "")
                session.recording = True
                logger.debug("Recording started (language=%s)", session.language)

            elif action == "stop":
                session.recording = False
                if not session.audio_chunks:
                    await websocket.send(json.dumps({"status": "error", "message": "No audio received."}))
                    continue

                audio_bytes = b"".join(session.audio_chunks)
                if len(audio_bytes) > max_bytes:
                    audio_mb = len(audio_bytes) / (1024 * 1024)
                    max_mb = max_bytes / (1024 * 1024)
                    await websocket.send(json.dumps({
                        "status": "error",
                        "message": f"Audio too large ({audio_mb:.1f} MB). Max allowed is {max_mb:.0f} MB.",
                    }))
                    continue

                # Re-discover whisper-server in case it restarted
                whisper_url = whisper_url_ref[0]
                if not whisper_url:
                    whisper_url = await discover_whisper_server()
                    whisper_url_ref[0] = whisper_url

                if not whisper_url:
                    await websocket.send(json.dumps({
                        "status": "error",
                        "message": (
                            "whisper-server not found on ports 8178–8199. "
                            "Please start OpenWhispr or run: "
                            "whisper-server --model <model.bin> --port 8178"
                        ),
                    }))
                    continue

                try:
                    text = await transcribe(
                        whisper_url, audio_bytes, session.language, session.prompt
                    )
                    await websocket.send(json.dumps({"status": "ok", "text": text}))
                    logger.info("Transcribed %d bytes → %d chars", len(audio_bytes), len(text))
                except aiohttp.ClientConnectionError:
                    whisper_url_ref[0] = None  # force re-discover next time
                    await websocket.send(json.dumps({
                        "status": "error",
                        "message": "whisper-server connection lost. Restart it and try again.",
                    }))
                except asyncio.TimeoutError:
                    await websocket.send(json.dumps({
                        "status": "error",
                        "message": "Transcription timed out. Try a shorter recording.",
                    }))
                except Exception as exc:
                    logger.exception("Transcription error")
                    await websocket.send(json.dumps({
                        "status": "error",
                        "message": f"Transcription error: {exc}",
                    }))
            else:
                await websocket.send(json.dumps({"status": "error", "message": f"Unknown action: {action!r}"}))

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as exc:
        logger.debug("Client disconnected with error: %s", exc)
    finally:
        logger.debug("Client disconnected: %s", remote)


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main(args):
    whisper_url_ref = [None]  # mutable so handler can update on reconnect

    # Initial discovery
    logger.info("Discovering whisper-server…")
    whisper_url_ref[0] = await discover_whisper_server(args.whisper_url)
    if whisper_url_ref[0]:
        logger.info("whisper-server ready at %s", whisper_url_ref[0])
    else:
        logger.warning(
            "whisper-server not found. Will retry on first connection. "
            "Start OpenWhispr or: whisper-server --model <model.bin> --port 8178"
        )

    host = "127.0.0.1"
    port = args.port
    max_bytes = args.max_bytes

    async def handler(ws):
        await handle_connection(ws, whisper_url_ref, max_bytes)

    logger.info("Whisper bridge listening on ws://%s:%d", host, port)
    logger.info("Connect your Django app or browser to ws://%s:%d", host, port)

    async with websockets.serve(handler, host, port):
        await asyncio.Future()  # run forever


def parse_args():
    parser = argparse.ArgumentParser(description="Local WebSocket bridge for OpenWhispr Django integration.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bridge WebSocket port (default: 9876)")
    parser.add_argument("--whisper-url", type=str, default=None, dest="whisper_url",
                        help="whisper-server base URL (default: auto-discover 8178-8199)")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, dest="max_bytes",
                        help="Max audio size in bytes (default: 10 MB)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        logger.info("Bridge stopped.")
        sys.exit(0)
