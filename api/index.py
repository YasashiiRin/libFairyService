"""Vercel entry point: the Python runtime serves this ASGI `app` (see vercel.json)."""

from app.main import app

__all__ = ["app"]
