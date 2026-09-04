"""FastAPI application for APIloop."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import load_config
from ..models import NormalizedRequest, NormalizedResponse
from ..providers.base import ProviderAdapterFactory
from ..routing.engine import RoutingEngine
from ..credentials.manager import CredentialManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    # Startup
    logger.info("APIloop starting up")
    config = load_config()
    app.state.config = config
    app.state.credential_manager = CredentialManager()
    app.state.routing_engine = RoutingEngine(
        strategy=config.routing.get("strategy", "health_aware"),
    )

    # Register providers
    for provider in config.providers.values():
        app.state.routing_engine.add_provider(provider)

    # Register models
    for model in config.models.values():
        app.state.routing_engine.add_models([model])

    logger.info(f"Loaded {len(config.providers)} providers, {len(config.models)} models")
    yield
    # Shutdown
    logger.info("APIloop shutting down")


app = FastAPI(
    title="APIloop",
    description="Provider-agnostic AI API gateway and router",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "APIloop",
        "version": "0.1.0",
        "description": "Provider-agnostic AI API gateway and router",
        "endpoints": {
            "chat": "/v1/chat/completions",
            "models": "/v1/models",
            "docs": "/docs",
        },
    }


@app.get("/v1/models")
async def list_models():
    """List available models (OpenAI-compatible endpoint)."""
    config = load_config()
    models = []
    for model in config.models.values():
        models.append({
            "id": model.id,
            "object": "model",
            "owned_by": model.provider_id,
            "permission": [],
        })
    return {"data": models, "object": "list"}


@app.post("/v1/chat/completions")
async def chat_completions(request: NormalizedRequest):
    """Handle chat completion requests (OpenAI-compatible endpoint)."""
    config = load_config()
    cm = app.state.credential_manager
    engine = app.state.routing_engine

    # Route the request
    route_result = await engine.route(request)
    if not route_result.success:
        raise HTTPException(
            status_code=503,
            detail=f"Routing failed: {route_result.error}",
        )

    # Get provider
    provider = config.get_provider(route_result.provider_id)
    if not provider:
        raise HTTPException(
            status_code=500,
            detail=f"Provider not found: {route_result.provider_id}",
        )

    # Get credential
    credential = cm.get_credential(route_result.provider_id)
    if provider.requires_authentication and not credential:
        raise HTTPException(
            status_code=401,
            detail=f"No credential configured for provider: {route_result.provider_id}",
        )

    # Create adapter
    adapter = ProviderAdapterFactory.create(provider, cm)

    try:
        # Execute the request
        response = await adapter.chat_completion(
            request=request,
            credential=credential,
            stream=False,
        )
        return response.model_dump()

    except Exception as e:
        logger.error(f"Provider error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Provider error: {str(e)[:200]}",  # Truncate to avoid leaking secrets
        )


@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(request: dict):
    """Handle audio transcription requests."""
    # TODO: Implement audio transcription support
    raise HTTPException(
        status_code=501,
        detail="Audio transcription not yet implemented",
    )


@app.post("/v1/embeddings")
async def embeddings(request: dict):
    """Handle embedding requests."""
    # TODO: Implement embeddings support
    raise HTTPException(
        status_code=501,
        detail="Embeddings not yet implemented",
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    config = load_config()
    providers_status = {}
    for pid, provider in config.providers.items():
        providers_status[pid] = {
            "health": provider.health.value,
            "url": provider.base_url,
        }

    return {
        "status": "healthy",
        "version": "0.1.0",
        "providers": providers_status,
    }


@app.get("/v1/health")
async def v1_health():
    """OpenAI-compatible health endpoint."""
    return await health()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "type": "api_error",
                "message": exc.detail,
            }
        },
    )


@app.get("/debug/config")
async def debug_config():
    """Debug endpoint for configuration (not for production)."""
    config = load_config()
    return {
        "providers": list(config.providers.keys()),
        "models": list(config.models.keys()),
        "routing_strategy": config.routing.get("strategy", "health_aware"),
        "api_host": config.api.get("host", "127.0.0.1"),
        "api_port": config.api.get("port", 8080),
    }
