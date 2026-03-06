"""
Tests for companion/server.py — HTTP endpoints and WebSocket.

Uses FastAPI's TestClient (httpx-based) so no live server is needed.
The AudioCapture and Transcriber are mocked to avoid hardware dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — patch AudioCapture and Transcriber before importing the app
# ---------------------------------------------------------------------------

def _make_mock_capture(is_recording: bool = False) -> MagicMock:
    cap = MagicMock()
    cap.is_recording = is_recording
    return cap


def _make_mock_transcriber(text: str = "hello world") -> MagicMock:
    tr = MagicMock()
    tr.model_name = "base"
    tr.transcribe.return_value = text
    tr.load.return_value = None
    return tr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """
    Return a TestClient with the lifespan context (model load) bypassed.
    AudioCapture and Transcriber are replaced with mocks.
    """
    import companion.server as srv

    mock_cap = _make_mock_capture()
    mock_tr  = _make_mock_transcriber()

    # Patch module-level singletons
    with (
        patch.object(srv, "_capture", mock_cap),
        patch.object(srv, "_transcriber", mock_tr),
    ):
        # TestClient handles lifespan automatically
        with TestClient(srv.app, raise_server_exceptions=True) as c:
            c._mock_capture     = mock_cap
            c._mock_transcriber = mock_tr
            yield c


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_not_recording(self, client):
        res = client.get("/status")
        assert res.status_code == 200
        data = res.json()
        assert data["recording"] is False
        assert data["model"] == "base"
        assert data["ready"] is True

    def test_status_while_recording(self, client):
        client._mock_capture.is_recording = True
        res = client.get("/status")
        assert res.status_code == 200
        assert res.json()["recording"] is True


# ---------------------------------------------------------------------------
# POST /start
# ---------------------------------------------------------------------------

class TestStart:
    def test_start_success(self, client):
        client._mock_capture.is_recording = False
        res = client.post("/start")
        assert res.status_code == 200
        assert res.json()["status"] == "recording"
        client._mock_capture.start.assert_called_once()

    def test_start_conflict_when_recording(self, client):
        client._mock_capture.is_recording = True
        res = client.post("/start")
        assert res.status_code == 409
        assert "already active" in res.json()["detail"].lower()

    def test_start_500_on_exception(self, client):
        client._mock_capture.is_recording = False
        client._mock_capture.start.side_effect = OSError("No microphone found")
        res = client.post("/start")
        assert res.status_code == 500
        assert "No microphone found" in res.json()["detail"]


# ---------------------------------------------------------------------------
# POST /stop
# ---------------------------------------------------------------------------

class TestStop:
    def _setup_wav(self, tmp_path: Path, duration_s: float = 2.0) -> Path:
        """Write a minimal valid WAV file so wave.open() succeeds."""
        import wave, struct

        wav = tmp_path / "test.wav"
        samples = int(16_000 * duration_s)
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16_000)
            wf.writeframes(b"\x00\x00" * samples)
        return wav

    def test_stop_success(self, client, tmp_path):
        wav = self._setup_wav(tmp_path)
        client._mock_capture.is_recording = True
        client._mock_capture.stop.return_value = wav
        client._mock_transcriber.transcribe.return_value = "hello world"

        res = client.post("/stop")

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "done"
        assert data["transcript"] == "hello world"
        assert data["duration_seconds"] == pytest.approx(2.0, abs=0.1)

    def test_stop_conflict_when_not_recording(self, client):
        client._mock_capture.is_recording = False
        res = client.post("/stop")
        assert res.status_code == 409
        assert "no active recording" in res.json()["detail"].lower()

    def test_stop_cleans_up_wav(self, client, tmp_path):
        wav = self._setup_wav(tmp_path)
        client._mock_capture.is_recording = True
        client._mock_capture.stop.return_value = wav

        client.post("/stop")

        # File should be deleted after transcription
        assert not wav.exists(), "Temp WAV should be deleted after /stop"

    def test_stop_cleans_up_wav_even_on_transcription_error(self, client, tmp_path):
        wav = self._setup_wav(tmp_path)
        client._mock_capture.is_recording = True
        client._mock_capture.stop.return_value = wav
        client._mock_transcriber.transcribe.side_effect = RuntimeError("model crashed")

        res = client.post("/stop")

        assert res.status_code == 500
        assert not wav.exists(), "Temp WAV must be deleted even on transcription failure"


# ---------------------------------------------------------------------------
# WebSocket /ws
# ---------------------------------------------------------------------------

class TestWebSocket:
    def test_ws_start_stop(self, client, tmp_path):
        import wave

        wav = tmp_path / "test.wav"
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16_000)
            wf.writeframes(b"\x00\x00" * 16_000)  # 1 s

        client._mock_capture.is_recording = False
        client._mock_capture.stop.return_value = wav
        client._mock_transcriber.transcribe.return_value = "hi there"

        with client.websocket_connect("/ws") as ws:
            ws.send_text("start")
            msg1 = json.loads(ws.receive_text())
            assert msg1["event"] == "recording_started"

            # Simulate the capture being in recording state before stop
            client._mock_capture.is_recording = True
            ws.send_text("stop")

            msg2 = json.loads(ws.receive_text())
            assert msg2["event"] == "transcribing"

            msg3 = json.loads(ws.receive_text())
            assert msg3["event"] == "result"
            assert msg3["transcript"] == "hi there"

    def test_ws_unknown_command(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text("foobar")
            msg = json.loads(ws.receive_text())
            assert msg["event"] == "error"
            assert "Unknown command" in msg["message"]

    def test_ws_start_when_already_recording(self, client):
        client._mock_capture.is_recording = True
        with client.websocket_connect("/ws") as ws:
            ws.send_text("start")
            msg = json.loads(ws.receive_text())
            assert msg["event"] == "error"
            assert "already recording" in msg["message"].lower()

    def test_ws_stop_when_not_recording(self, client):
        client._mock_capture.is_recording = False
        with client.websocket_connect("/ws") as ws:
            ws.send_text("stop")
            msg = json.loads(ws.receive_text())
            assert msg["event"] == "error"
            assert "not recording" in msg["message"].lower()
