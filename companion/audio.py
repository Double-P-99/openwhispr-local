"""
audio.py — Microphone capture for the OpenWhispr companion service.

Records from the default input device using sounddevice and saves the
audio to a temporary WAV file that the transcriber can process.
"""

from __future__ import annotations

import logging
import queue
import tempfile
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Audio format constants — Whisper expects 16 kHz mono PCM.
SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"


class AudioCapture:
    """
    Thread-safe microphone recorder.

    Usage::

        capture = AudioCapture()
        capture.start()
        # ... user speaks ...
        wav_path = capture.stop()   # blocks until the file is written
        # process wav_path with the transcriber, then delete it
    """

    def __init__(self) -> None:
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Optional[object] = None  # sounddevice.InputStream (lazy import)
        self._frames: list[np.ndarray] = []
        self._recording = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording.is_set()

    def start(self) -> None:
        """Open the microphone stream and begin buffering audio."""
        try:
            import sounddevice as sd  # noqa: PLC0415  (lazy — avoids PortAudio at module load)
        except OSError as exc:
            raise RuntimeError(
                "PortAudio library not found.  "
                "Install it with: sudo apt install portaudio19-dev  (Linux)  "
                "or: brew install portaudio  (macOS)"
            ) from exc

        with self._lock:
            if self._recording.is_set():
                raise RuntimeError("Already recording")

            self._frames = []
            self._recording.set()

            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=self._callback,
            )
            self._stream.start()
            logger.info("Microphone capture started (16 kHz, mono, int16)")

    def stop(self) -> Path:
        """
        Stop recording and flush audio to a temporary WAV file.

        Returns
        -------
        Path
            Absolute path to the written WAV file.  The caller is
            responsible for deleting it after transcription.
        """
        with self._lock:
            if not self._recording.is_set():
                raise RuntimeError("Not recording")

            self._recording.clear()

            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None

        return self._write_wav()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time: object,
        status: object,  # sounddevice.CallbackFlags (lazy import, use object)
    ) -> None:
        if status:
            logger.warning("sounddevice status: %s", status)
        if self._recording.is_set():
            self._frames.append(indata.copy())

    def _write_wav(self) -> Path:
        """Concatenate captured frames and write a 16-bit mono WAV."""
        if not self._frames:
            raise ValueError("No audio captured — did the microphone work?")

        audio = np.concatenate(self._frames, axis=0)

        tmp = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False, prefix="openwhispr_"
        )
        tmp.close()
        wav_path = Path(tmp.name)

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 → 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())

        logger.info("WAV written: %s (%.1f s)", wav_path, len(audio) / SAMPLE_RATE)
        return wav_path
