"""WebSocket URL routing for dictation_demo."""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/dictation/$", consumers.DictationConsumer.as_asgi()),
]
