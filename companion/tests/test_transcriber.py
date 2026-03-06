"""
Tests for companion/transcriber.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from companion.transcriber import DEFAULT_MODEL, Transcriber


class TestTranscriber:
    """Unit tests for Transcriber — faster-whisper is mocked throughout."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def test_default_model_name(self):
        t = Transcriber()
        assert t.model_name == DEFAULT_MODEL

    def test_custom_model_name(self):
        t = Transcriber(model_name="small")
        assert t.model_name == "small"

    def test_not_loaded_initially(self):
        t = Transcriber()
        assert t._model is None

    # ------------------------------------------------------------------
    # _load_model
    # ------------------------------------------------------------------

    def test_load_model_raises_without_package(self):
        t = Transcriber()
        with patch.dict("sys.modules", {"faster_whisper": None}):
            with pytest.raises(RuntimeError, match="faster-whisper is not installed"):
                t._load_model()

    def test_load_model_calls_whisper_model(self):
        t = Transcriber(model_name="tiny", device="cpu", compute_type="int8")

        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        mock_fw = MagicMock()
        mock_fw.WhisperModel = mock_cls

        with patch.dict("sys.modules", {"faster_whisper": mock_fw}):
            t._load_model()

        mock_cls.assert_called_once_with("tiny", device="cpu", compute_type="int8")
        assert t._model is mock_instance

    def test_load_called_once_on_repeated_transcribe(self, tmp_path):
        """Model should only be loaded once even with multiple transcribe calls."""
        t = Transcriber()

        mock_segment = MagicMock()
        mock_segment.text = "hello"
        mock_info = MagicMock()
        mock_info.language = "en"
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], mock_info)

        with patch.object(t, "_load_model", wraps=lambda: setattr(t, "_model", mock_model)) as mock_load:
            wav = tmp_path / "test.wav"
            wav.write_bytes(b"")  # empty file — mock doesn't actually read it
            t.transcribe(wav)
            t.transcribe(wav)

        mock_load.assert_called_once()

    # ------------------------------------------------------------------
    # transcribe()
    # ------------------------------------------------------------------

    def _make_transcriber_with_mock(self, text: str = "hello world") -> tuple[Transcriber, MagicMock]:
        """Return a Transcriber with the internal model pre-mocked."""
        t = Transcriber()

        mock_segment = MagicMock()
        mock_segment.text = " " + text
        mock_info = MagicMock()
        mock_info.language = "en"

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], mock_info)

        t._model = mock_model
        return t, mock_model

    def test_transcribe_returns_stripped_text(self, tmp_path):
        t, _ = self._make_transcriber_with_mock("  hello world  ")
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"")

        result = t.transcribe(wav)
        assert result == "hello world"

    def test_transcribe_passes_language(self, tmp_path):
        t, mock_model = self._make_transcriber_with_mock("bonjour")
        t.language = "fr"
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"")

        t.transcribe(wav)

        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs.get("language") == "fr"

    def test_transcribe_passes_prompt(self, tmp_path):
        t, mock_model = self._make_transcriber_with_mock("test")
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"")

        t.transcribe(wav, prompt="OpenWhispr Django FastAPI")

        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs.get("initial_prompt") == "OpenWhispr Django FastAPI"

    def test_transcribe_no_prompt_omits_initial_prompt(self, tmp_path):
        t, mock_model = self._make_transcriber_with_mock("test")
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"")

        t.transcribe(wav, prompt="")

        call_kwargs = mock_model.transcribe.call_args[1]
        assert "initial_prompt" not in call_kwargs

    def test_transcribe_empty_segments_returns_empty_string(self, tmp_path):
        t = Transcriber()
        mock_info = MagicMock()
        mock_info.language = "en"
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], mock_info)
        t._model = mock_model

        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"")

        result = t.transcribe(wav)
        assert result == ""

    def test_transcribe_joins_multiple_segments(self, tmp_path):
        t = Transcriber()

        seg1 = MagicMock()
        seg1.text = "Hello"
        seg2 = MagicMock()
        seg2.text = " world"   # Whisper segments carry their own leading space
        mock_info = MagicMock()
        mock_info.language = "en"

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([seg1, seg2], mock_info)
        t._model = mock_model

        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"")

        result = t.transcribe(wav)
        # "Hello" + " world" concatenated then stripped → "Hello world"
        assert result == "Hello world"
