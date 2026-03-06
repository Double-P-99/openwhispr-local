"""
Tests for dictation_demo views (Django Channels / transcription proxy).

These tests mock the whisper-server HTTP call so they can run without the
whisper-server binary or any audio hardware.
"""

import io
import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse


class IndexViewTest(TestCase):
    """The index view renders the dictation page."""

    def test_index_returns_200(self):
        client = Client(enforce_csrf_checks=False)
        response = client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)

    def test_index_contains_start_button(self):
        client = Client(enforce_csrf_checks=False)
        response = client.get(reverse("index"))
        self.assertContains(response, "startDictation")

    def test_index_contains_both_textareas(self):
        client = Client(enforce_csrf_checks=False)
        response = client.get(reverse("index"))
        content = response.content.decode()
        self.assertIn('id="field-1"', content)
        self.assertIn('id="field-2"', content)


class TranscribeProxyViewTest(TestCase):
    """Tests for the /api/transcribe/ proxy endpoint."""

    def _make_audio_file(self, content: bytes = b"FAKE_AUDIO", name: str = "test.webm"):
        return io.BytesIO(content)

    def _post_audio(self, client, audio_bytes=b"FAKE_AUDIO", language="auto", enforce_csrf=True):
        """Helper: POST audio to the transcribe endpoint."""
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "test.webm"
        return client.post(
            reverse("transcribe_proxy"),
            data={"audio": audio_file, "language": language},
            format="multipart",
        )

    def test_no_audio_returns_400(self):
        client = Client(enforce_csrf_checks=False)
        response = client.post(reverse("transcribe_proxy"), data={})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    @override_settings(WHISPER_MAX_AUDIO_BYTES=10)
    def test_oversized_audio_returns_413(self):
        client = Client(enforce_csrf_checks=False)
        audio_file = io.BytesIO(b"A" * 20)
        audio_file.name = "big.webm"
        response = client.post(
            reverse("transcribe_proxy"),
            data={"audio": audio_file},
        )
        self.assertEqual(response.status_code, 413)

    @patch("dictation_demo.views.requests.post")
    def test_successful_transcription_returns_text(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "Hello world"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        client = Client(enforce_csrf_checks=False)
        audio_file = io.BytesIO(b"FAKE_AUDIO_DATA")
        audio_file.name = "test.webm"
        response = client.post(
            reverse("transcribe_proxy"),
            data={"audio": audio_file},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["text"], "Hello world")

    @patch("dictation_demo.views.requests.post")
    def test_whisper_server_connection_error_returns_503(self, mock_post):
        import requests as req_lib

        mock_post.side_effect = req_lib.exceptions.ConnectionError("refused")

        client = Client(enforce_csrf_checks=False)
        audio_file = io.BytesIO(b"FAKE_AUDIO_DATA")
        audio_file.name = "test.webm"
        response = client.post(
            reverse("transcribe_proxy"),
            data={"audio": audio_file},
        )
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.content)
        self.assertIn("error", data)
        self.assertIn("whisper-server", data["error"])

    @patch("dictation_demo.views.requests.post")
    def test_timeout_returns_504(self, mock_post):
        import requests as req_lib

        mock_post.side_effect = req_lib.exceptions.Timeout()

        client = Client(enforce_csrf_checks=False)
        audio_file = io.BytesIO(b"FAKE_AUDIO_DATA")
        audio_file.name = "test.webm"
        response = client.post(
            reverse("transcribe_proxy"),
            data={"audio": audio_file},
        )
        self.assertEqual(response.status_code, 504)

    def test_get_not_allowed(self):
        client = Client(enforce_csrf_checks=False)
        response = client.get(reverse("transcribe_proxy"))
        self.assertEqual(response.status_code, 405)

    @patch("dictation_demo.views.requests.post")
    def test_language_parameter_forwarded(self, mock_post):
        """The language parameter should be passed to whisper-server."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "Hola mundo"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        client = Client(enforce_csrf_checks=False)
        audio_file = io.BytesIO(b"FAKE")
        audio_file.name = "test.webm"
        client.post(
            reverse("transcribe_proxy"),
            data={"audio": audio_file, "language": "es"},
        )

        # Verify 'language' was included in the data sent to whisper-server.
        _, kwargs = mock_post.call_args
        data_sent = kwargs.get("data", {})
        self.assertEqual(data_sent.get("language"), "es")

    @patch("dictation_demo.views.requests.post")
    def test_auto_language_not_forwarded(self, mock_post):
        """'auto' language should not be sent to whisper-server."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "Hello"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        client = Client(enforce_csrf_checks=False)
        audio_file = io.BytesIO(b"FAKE")
        audio_file.name = "test.webm"
        client.post(
            reverse("transcribe_proxy"),
            data={"audio": audio_file, "language": "auto"},
        )

        _, kwargs = mock_post.call_args
        data_sent = kwargs.get("data", {})
        self.assertNotIn("language", data_sent)
