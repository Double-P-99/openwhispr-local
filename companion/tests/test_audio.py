"""
Tests for companion/audio.py
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Patch sounddevice before importing companion.audio so that PortAudio is
# never required in CI or headless environments.
# ---------------------------------------------------------------------------
_mock_sd = MagicMock()
sys.modules.setdefault("sounddevice", _mock_sd)

from companion.audio import CHANNELS, DTYPE, SAMPLE_RATE, AudioCapture  # noqa: E402


class TestAudioCapture:
    """Unit tests for AudioCapture — microphone is mocked throughout."""

    def _make_capture_with_frames(self, seconds: float = 1.0) -> AudioCapture:
        """Return an AudioCapture pre-loaded with synthetic audio frames."""
        cap = AudioCapture()
        # Simulate 1 second of silence at 16 kHz
        samples = int(SAMPLE_RATE * seconds)
        frame = np.zeros((samples, CHANNELS), dtype=DTYPE)
        cap._frames = [frame]
        return cap

    # ------------------------------------------------------------------
    # is_recording property
    # ------------------------------------------------------------------

    def test_not_recording_initially(self):
        cap = AudioCapture()
        assert cap.is_recording is False

    def test_is_recording_after_event_set(self):
        cap = AudioCapture()
        cap._recording.set()
        assert cap.is_recording is True

    # ------------------------------------------------------------------
    # start()
    # ------------------------------------------------------------------

    def test_start_opens_stream(self):
        cap = AudioCapture()
        mock_stream = MagicMock()
        mock_sd = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        with patch.dict("sys.modules", {"sounddevice": mock_sd}):
            cap.start()

        assert cap.is_recording is True
        mock_stream.start.assert_called_once()

    def test_start_raises_when_already_recording(self):
        cap = AudioCapture()
        mock_stream = MagicMock()
        mock_sd = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        with patch.dict("sys.modules", {"sounddevice": mock_sd}):
            cap.start()
            with pytest.raises(RuntimeError, match="Already recording"):
                cap.start()

    # ------------------------------------------------------------------
    # stop()
    # ------------------------------------------------------------------

    def test_stop_writes_wav_file(self, tmp_path):
        cap = self._make_capture_with_frames(seconds=0.5)
        cap._recording.set()  # simulate "recording" state

        with patch("companion.audio.tempfile.NamedTemporaryFile") as mock_tmp:
            # Point to a real temp path so wave.open works
            out = tmp_path / "test.wav"
            mock_file = MagicMock()
            mock_file.name = str(out)
            mock_tmp.return_value = mock_file

            wav_path = cap.stop()

        assert wav_path == out
        assert out.exists(), "WAV file should have been written"

        with wave.open(str(out), "rb") as wf:
            assert wf.getnchannels() == CHANNELS
            assert wf.getframerate() == SAMPLE_RATE
            assert wf.getsampwidth() == 2  # int16

    def test_stop_raises_when_not_recording(self):
        cap = AudioCapture()
        with pytest.raises(RuntimeError, match="Not recording"):
            cap.stop()

    def test_stop_raises_with_no_audio(self):
        cap = AudioCapture()
        cap._recording.set()
        cap._frames = []  # empty
        mock_stream = MagicMock()
        cap._stream = mock_stream

        with pytest.raises(ValueError, match="No audio captured"):
            cap.stop()

    # ------------------------------------------------------------------
    # _write_wav
    # ------------------------------------------------------------------

    def test_write_wav_produces_valid_file(self, tmp_path):
        cap = self._make_capture_with_frames(seconds=2.0)

        with patch("companion.audio.tempfile.NamedTemporaryFile") as mock_tmp:
            out = tmp_path / "output.wav"
            mock_file = MagicMock()
            mock_file.name = str(out)
            mock_tmp.return_value = mock_file

            result = cap._write_wav()

        assert result == out
        with wave.open(str(out), "rb") as wf:
            assert wf.getnframes() == SAMPLE_RATE * 2  # 2 seconds
