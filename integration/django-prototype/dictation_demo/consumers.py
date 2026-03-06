"""
Django Channels WebSocket consumer for the dictation bridge.

This consumer acts as a relay: the browser connects via WebSocket, sends
raw audio bytes, and the consumer forwards them to the local whisper-server.
The transcription result is sent back to the browser as a JSON text frame.

Architecture:
    Browser (WebSocket) ─► DictationConsumer ─► whisper-server HTTP
                                  ◄──────────────────── JSON {text}

This is an alternative to Path 2 (direct bridge) that keeps all traffic
inside Django Channels, enabling standard Django auth and CSRF on the
WebSocket upgrade handshake.

Limitations:
- Requires daphne (or uvicorn with ASGI) as the app server; gunicorn does
  not support WebSockets.
- Audio is buffered in memory; very long recordings (> ~5 minutes) may
  consume significant RAM.
- Requires whisper-server to be running locally.
"""

import io
import json
import logging

import aiohttp
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

logger = logging.getLogger(__name__)


class DictationConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer that accepts audio bytes and returns a transcription.

    Protocol:
        Client → Server:  binary frame containing raw audio data (webm/wav/ogg)
        Server → Client:  text frame containing JSON:
                          {"status": "ok", "text": "..."}
                          or {"status": "error", "message": "..."}
    """

    async def connect(self):
        await self.accept()
        self._audio_chunks: list[bytes] = []
        logger.debug("DictationConsumer: client connected")

    async def disconnect(self, close_code):
        logger.debug("DictationConsumer: client disconnected (code=%s)", close_code)
        self._audio_chunks = []

    async def receive(self, text_data=None, bytes_data=None):
        if text_data:
            try:
                msg = json.loads(text_data)
            except json.JSONDecodeError:
                await self._send_error("Invalid JSON control message.")
                return

            action = msg.get("action")
            if action == "start":
                self._audio_chunks = []
                self._language = msg.get("language", "auto")
                self._prompt = msg.get("prompt", "")
                logger.debug("DictationConsumer: recording started (language=%s)", self._language)
            elif action == "stop":
                await self._finalize_transcription()
            else:
                await self._send_error(f"Unknown action: {action!r}")

        elif bytes_data:
            # Accumulate audio chunks
            self._audio_chunks.append(bytes_data)

    async def _finalize_transcription(self):
        """Assemble buffered audio, POST to whisper-server using aiohttp, return result."""
        if not self._audio_chunks:
            await self._send_error("No audio data received.")
            return

        audio_bytes = b"".join(self._audio_chunks)
        max_bytes = settings.WHISPER_MAX_AUDIO_BYTES
        if len(audio_bytes) > max_bytes:
            audio_mb = len(audio_bytes) / (1024 * 1024)
            max_mb = max_bytes / (1024 * 1024)
            await self._send_error(
                f"Audio too large ({audio_mb:.1f} MB). Max allowed is {max_mb:.0f} MB."
            )
            return

        whisper_url = f"{settings.WHISPER_SERVER_URL}/inference"
        language = getattr(self, "_language", "auto")
        prompt = getattr(self, "_prompt", "")

        try:
            text = await self._call_whisper_server(whisper_url, audio_bytes, language, prompt)
            await self.send(text_data=json.dumps({"status": "ok", "text": text}))
        except aiohttp.ClientConnectionError:
            await self._send_error(
                "whisper-server is not running. Start OpenWhispr or the standalone whisper-server."
            )
        except aiohttp.ServerTimeoutError:
            await self._send_error("Transcription timed out.")
        except Exception as exc:
            logger.exception("DictationConsumer: transcription error")
            await self._send_error(f"Server error: {exc}")

    @staticmethod
    async def _call_whisper_server(
        url: str, audio_bytes: bytes, language: str, prompt: str
    ) -> str:
        """POST audio to whisper-server using aiohttp (fully async, no thread pool needed)."""
        form = aiohttp.FormData()
        form.add_field(
            "file",
            io.BytesIO(audio_bytes),
            filename="audio.webm",
            content_type="audio/webm",
        )
        form.add_field("response_format", "json")
        if language and language != "auto":
            form.add_field("language", language)
        if prompt:
            form.add_field("prompt", prompt)

        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form) as response:
                response.raise_for_status()
                result = await response.json()
                return result.get("text", "").strip()

    async def _send_error(self, message: str):
        await self.send(text_data=json.dumps({"status": "error", "message": message}))
