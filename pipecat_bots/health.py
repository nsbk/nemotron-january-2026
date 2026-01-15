"""
Health endpoint for the Nemotron bot.

Provides /health endpoint for container health checks and monitoring.
This module integrates with pipecat's FastAPI-based WebRTC transport.

Usage:
    from health import add_health_routes, check_services_health

    # Add to existing FastAPI app
    add_health_routes(app)

    # Or use standalone health check
    status = await check_services_health()
"""

import os
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger


# Service URLs from environment
NVIDIA_ASR_URL = os.getenv("NVIDIA_ASR_URL", "ws://localhost:8080")
NVIDIA_TTS_URL = os.getenv("NVIDIA_TTS_URL", "http://localhost:8001")
NVIDIA_LLAMA_CPP_URL = os.getenv("NVIDIA_LLAMA_CPP_URL", "http://localhost:8000")


async def check_service_health(url: str, name: str) -> dict:
    """Check health of a single service."""
    # Convert WebSocket URL to HTTP for health check
    health_url = url.replace("ws://", "http://").replace("wss://", "https://")
    if not health_url.endswith("/health"):
        health_url = health_url.rstrip("/") + "/health"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(health_url)
            if response.status_code == 200:
                return {"name": name, "status": "healthy", "url": url}
            else:
                return {"name": name, "status": "unhealthy", "url": url, "error": f"HTTP {response.status_code}"}
    except httpx.TimeoutException:
        return {"name": name, "status": "unhealthy", "url": url, "error": "timeout"}
    except Exception as e:
        return {"name": name, "status": "unhealthy", "url": url, "error": str(e)}


async def check_services_health() -> dict:
    """Check health of all backend services."""
    services = [
        (NVIDIA_ASR_URL, "asr"),
        (NVIDIA_TTS_URL, "tts"),
        (NVIDIA_LLAMA_CPP_URL, "llm"),
    ]

    results = []
    all_healthy = True

    for url, name in services:
        result = await check_service_health(url, name)
        results.append(result)
        if result["status"] != "healthy":
            all_healthy = False

    return {
        "status": "healthy" if all_healthy else "degraded",
        "services": results,
    }


def add_health_routes(app: FastAPI):
    """Add health check routes to a FastAPI app."""

    @app.get("/health")
    async def health():
        """
        Health check endpoint.

        Returns:
            - 200 OK if bot is running (regardless of backend service status)
            - Response includes status of backend services for debugging
        """
        try:
            services_health = await check_services_health()
            return JSONResponse(
                content={
                    "status": "ok",
                    "bot": "running",
                    "backends": services_health,
                },
                status_code=200,
            )
        except Exception as e:
            logger.error(f"Health check error: {e}")
            return JSONResponse(
                content={
                    "status": "ok",
                    "bot": "running",
                    "backends": {"status": "unknown", "error": str(e)},
                },
                status_code=200,
            )

    @app.get("/ready")
    async def ready():
        """
        Readiness check endpoint.

        Returns:
            - 200 OK if bot AND all backend services are healthy
            - 503 Service Unavailable if any backend service is unhealthy
        """
        try:
            services_health = await check_services_health()
            if services_health["status"] == "healthy":
                return JSONResponse(
                    content={"status": "ready", "backends": services_health},
                    status_code=200,
                )
            else:
                return JSONResponse(
                    content={"status": "not_ready", "backends": services_health},
                    status_code=503,
                )
        except Exception as e:
            logger.error(f"Readiness check error: {e}")
            return JSONResponse(
                content={"status": "not_ready", "error": str(e)},
                status_code=503,
            )

    logger.info("Added /health and /ready endpoints")
    return app
