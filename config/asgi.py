"""ASGI entry point.

The FastAPI app is constructed via the factory in `config.main:create_app`.
"""

from __future__ import annotations

from fastapi import FastAPI

from config.main import create_app

application: FastAPI = create_app()
