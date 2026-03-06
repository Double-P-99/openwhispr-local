"""
Django settings for dictation_project.

This is a minimal prototype configuration. Do not use these settings in
production without hardening SECRET_KEY, DEBUG, ALLOWED_HOSTS, databases,
and CSRF settings.
"""

import os
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    # Development-only fallback — never use in production without setting DJANGO_SECRET_KEY.
    f"dev-only-insecure-{uuid.uuid4()}",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "channels",
    "dictation_demo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dictation_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

# Use ASGI / Channels for WebSocket support
ASGI_APPLICATION = "dictation_project.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django Channels: use in-memory channel layer for prototype (no Redis required).
# For production, replace with RedisChannelLayer.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# The local whisper-server base URL. The Django backend POSTs audio here when
# the browser uploads audio directly to Django (Path 1 proxy mode).
# Override via environment variable WHISPER_SERVER_URL.
WHISPER_SERVER_URL = os.environ.get("WHISPER_SERVER_URL", "http://127.0.0.1:8178")

# Maximum audio upload size in bytes (10 MB default).
WHISPER_MAX_AUDIO_BYTES = int(os.environ.get("WHISPER_MAX_AUDIO_BYTES", 10 * 1024 * 1024))

# The local WebSocket bridge URL (Path 2: browser connects here directly).
# Override via environment variable WHISPER_BRIDGE_WS_URL.
WHISPER_BRIDGE_WS_URL = os.environ.get("WHISPER_BRIDGE_WS_URL", "ws://127.0.0.1:9876")
