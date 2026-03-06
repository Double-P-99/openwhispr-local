"""
dictation/views.py — Minimal views for the push-to-talk dictation demo.
"""

from django.conf import settings
from django.shortcuts import render


def dictation_page(request):
    """Render the push-to-talk dictation UI."""
    companion_url = getattr(settings, "COMPANION_URL", "http://127.0.0.1:8765")
    return render(
        request,
        "dictation/dictation.html",
        {"companion_url": companion_url},
    )
