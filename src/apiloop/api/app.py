"""FastAPI application for APIloop."""

from __future__ import annotations

import logging
import secrets
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import load_config
from ..models import NormalizedRequest, NormalizedResponse
from ..providers.base import ProviderAdapterFactory
from ..routing.engine import RoutingEngine
from ..credentials.manager import CredentialManager

logger = logging.getLogger(__name__)


def _load_or_create_gateway_key(key_path: Path) -> str:
    """Load the gateway's own inbound API key, generating one on first run.

    Stored the same way as the credential master key (owner-only file,
    not inside config.yaml) so it doesn't end up in a file that's
    routinely edited/diffed/backed up alongside non-secret config.
    """
    if key_path.exists():
        return key_path.read_text().strip()
    key = secrets.token_urlsafe(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key)
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return key


async def require_api_key(
    request: Request, authorization: Optional[str] = Header(default=None)
) -> None:
    """FastAPI dependency gating a route behind the gateway's own API key.

    This authenticates *callers of APIloop*, separate from the per-provider
    credentials APIloop uses to call out to OpenAI/Anthropic/etc.
    """
    expected = getattr(request.app.state, "gateway_api_key", None)
    if not expected:
        raise HTTPException(status_code=503, detail="Gateway API key not configured")

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]

    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


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

    gateway_key_path = config.config_path.parent / ".gateway_key"
    key_existed = gateway_key_path.exists()
    app.state.gateway_api_key = _load_or_create_gateway_key(gateway_key_path)
    if not key_existed:
        logger.warning(
            f"Generated new gateway API key at {gateway_key_path} "
            f"(shown once): {app.state.gateway_api_key}"
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


@app.get("/v1/models", dependencies=[Depends(require_api_key)])
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


@app.post("/v1/chat/completions", dependencies=[Depends(require_api_key)])
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


@app.post("/v1/audio/transcriptions", dependencies=[Depends(require_api_key)])
async def audio_transcriptions(request: dict):
    """Handle audio transcription requests."""
    # TODO: Implement audio transcription support
    raise HTTPException(
        status_code=501,
        detail="Audio transcription not yet implemented",
    )


@app.post("/v1/embeddings", dependencies=[Depends(require_api_key)])
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


@app.get("/debug/config", dependencies=[Depends(require_api_key)])
async def debug_config():
    """Debug endpoint for configuration. Requires the gateway API key AND an
    explicit opt-in (config.api.debug_endpoints_enabled) - off by default."""
    config = load_config()
    if not config.api.get("debug_endpoints_enabled", False):
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "providers": list(config.providers.keys()),
        "models": list(config.models.keys()),
        "routing_strategy": config.routing.get("strategy", "health_aware"),
        "api_host": config.api.get("host", "127.0.0.1"),
        "api_port": config.api.get("port", 8080),
    }
