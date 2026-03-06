"""
dictation/urls.py
"""

from django.urls import path

from . import views

app_name = "dictation"

urlpatterns = [
    path("", views.dictation_page, name="dictation"),
]
