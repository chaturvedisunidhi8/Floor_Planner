"""Aggregates every v1 route onto one router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import generation, health, options, templates

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(options.router)
api_router.include_router(templates.router)
api_router.include_router(generation.router)
