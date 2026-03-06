"""
Views for dictation_demo.

Path 1 (proxy): Browser uploads audio to /api/transcribe/ → Django POSTs it
to the local whisper-server → returns JSON {text: "..."}.

Path 2 (bridge): Browser connects directly to ws://127.0.0.1:9876 (the local
whisper bridge). Django just serves the HTML page. The DictationConsumer below
is an alternative bridge that keeps audio traffic inside Django Channels.
"""

import io
import logging

import requests
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


def index(request):
    """Render the demo dictation page."""
    bridge_ws_url = settings.WHISPER_BRIDGE_WS_URL
    return render(
        request,
        "dictation_demo/index.html",
        {"bridge_ws_url": bridge_ws_url},
    )


@require_POST
def transcribe_proxy(request):
    """
    Path 1 proxy endpoint.

    Accepts a multipart POST with an 'audio' file field (any format that
    whisper-server / FFmpeg can handle: webm, ogg, wav, mp4, etc.) and an
    optional 'language' field (two-letter ISO code or 'auto').

    Proxies the audio to the local whisper-server and returns:
        {"text": "transcription result"}

    Limitations:
    - Requires whisper-server to be running on localhost (started by
      OpenWhispr desktop app or as a standalone daemon).
    - Audio data travels: browser → Django → whisper-server (two hops).
    - Latency is slightly higher than the direct bridge (Path 2).
    - CSRF protection applies; the JavaScript must include the CSRF token.
    """
    audio_file = request.FILES.get("audio")
    if audio_file is None:
        return JsonResponse({"error": "No audio file provided."}, status=400)

    max_bytes = settings.WHISPER_MAX_AUDIO_BYTES
    if audio_file.size > max_bytes:
        return JsonResponse(
            {"error": f"Audio file too large (max {max_bytes // (1024*1024)} MB)."},
            status=413,
        )

    language = request.POST.get("language", "auto")
    prompt = request.POST.get("prompt", "")

    whisper_url = f"{settings.WHISPER_SERVER_URL}/inference"

    try:
        audio_bytes = audio_file.read()
        files = {
            "file": (audio_file.name or "audio.webm", io.BytesIO(audio_bytes), audio_file.content_type or "audio/webm"),
        }
        data = {
            "response_format": "json",
        }
        if language and language != "auto":
            data["language"] = language
        if prompt:
            data["prompt"] = prompt

        response = requests.post(
            whisper_url,
            files=files,
            data=data,
            timeout=120,
        )
        response.raise_for_status()
        result = response.json()
        text = result.get("text", "").strip()
        return JsonResponse({"text": text})

    except requests.exceptions.ConnectionError:
        logger.warning("whisper-server not reachable at %s", whisper_url)
        return JsonResponse(
            {
                "error": (
                    "whisper-server is not running. Please start OpenWhispr or "
                    "run: whisper-server --model <model.bin> --port 8178"
                )
            },
            status=503,
        )
    except requests.exceptions.Timeout:
        logger.warning("whisper-server timed out")
        return JsonResponse({"error": "Transcription timed out. Try a shorter recording."}, status=504)
    except requests.exceptions.HTTPError as exc:
        logger.error("whisper-server returned error: %s", exc)
        return JsonResponse({"error": f"whisper-server error: {exc.response.status_code}"}, status=502)
    except Exception as exc:
        logger.exception("Unexpected error in transcribe_proxy")
        return JsonResponse({"error": "Unexpected server error."}, status=500)
