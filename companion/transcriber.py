"""
transcriber.py — Speech-to-text integration for the OpenWhispr companion.

Primary engine: faster-whisper (CTranslate2-based, efficient CPU/GPU inference).
The transcriber loads the model once at startup and exposes a single
``transcribe(wav_path)`` method that returns a plain text string.

Supported models (Whisper):
  tiny, base, small, medium, large-v2, large-v3, distil-large-v3, …

See https://github.com/SYSTRAN/faster-whisper for full documentation.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default model — "base" strikes a good balance between speed and accuracy.
# Override via the WHISPER_MODEL environment variable.
DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "base")

# Device selection — "auto" picks CUDA when available, otherwise CPU.
DEFAULT_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")

# Compute type — "int8" is fastest on CPU; "float16" is best on GPU.
DEFAULT_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")


class Transcriber:
    """
    Wraps faster-whisper and returns raw transcription text.

    The model is lazy-loaded on the first call to ``transcribe()`` so that
    the HTTP server starts quickly even on slow machines.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        language: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language  # None → auto-detect
        self._model = None  # loaded lazily

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Eagerly load the model (call during server startup for faster first request)."""
        if self._model is None:
            self._load_model()

    def transcribe(self, wav_path: Path, prompt: str = "") -> str:
        """
        Transcribe *wav_path* and return plain text.

        Parameters
        ----------
        wav_path:
            Path to a 16 kHz mono WAV file.
        prompt:
            Optional context/vocabulary hint (custom dictionary words joined
            by spaces work well here).

        Returns
        -------
        str
            The transcribed text, stripped of leading/trailing whitespace.
            Returns an empty string when no speech is detected.
        """
        if self._model is None:
            self._load_model()

        logger.info("Transcribing %s with model=%s", wav_path, self.model_name)

        kwargs: dict = {
            "beam_size": 5,
            "vad_filter": True,  # skip non-speech segments automatically
        }
        if self.language:
            kwargs["language"] = self.language
        if prompt:
            kwargs["initial_prompt"] = prompt

        segments, info = self._model.transcribe(str(wav_path), **kwargs)

        # Whisper segments already include leading/trailing whitespace between
        # words, so concatenate directly (not space-joined) then strip.
        text_parts = [seg.text for seg in segments]
        result = "".join(text_parts).strip()

        logger.info(
            "Transcription complete: detected_language=%s, text=%r",
            info.language,
            result[:120],
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed.  "
                "Run: pip install faster-whisper"
            ) from exc

        logger.info(
            "Loading faster-whisper model '%s' (device=%s, compute_type=%s) …",
            self.model_name,
            self.device,
            self.compute_type,
        )
        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("Model loaded successfully.")
