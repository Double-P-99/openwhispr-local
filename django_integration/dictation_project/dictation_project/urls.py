"""
URL configuration for dictation_project.
"""

from django.urls import include, path

urlpatterns = [
    path("", include("dictation.urls")),
]
