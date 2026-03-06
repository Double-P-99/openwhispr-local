"""URL patterns for dictation_demo."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/transcribe/", views.transcribe_proxy, name="transcribe_proxy"),
]
