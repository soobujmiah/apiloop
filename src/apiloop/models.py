"""Core data models for APIloop."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, SecretStr, field_serializer


# ─── Capability Model ───────────────────────────────────────────────────────

class Capability(BaseModel):
    """A single capability that a provider or model may support."""

    name: str  # e.g. "chat", "embeddings", "image_generation", "audio_transcription"
    description: str = ""
    supported: bool = False


class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class InputModality(BaseModel):
    modality: Modality
    formats: list[str] = Field(default_factory=list)  # e.g. ["png", "jpeg", "mp3"]


class OutputModality(BaseModel):
    modality: Modality
    formats: list[str] = Field(default_factory=list)


# ─── Provider Descriptor ────────────────────────────────────────────────────

class ProviderKind(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"


class ProviderHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


class ProviderDescriptor(BaseModel):
    """Describes an AI provider and its capabilities."""

    id: str  # unique identifier, e.g. "openai", "ollama-local"
    name: str  # human-readable name
    kind: ProviderKind
    # Which adapter implementation to use (registry key in ProviderAdapterFactory),
    # e.g. "openai_compatible", "anthropic". Distinct from `kind` (local/remote/hybrid),
    # which describes deployment topology, not the vendor API shape.
    adapter_type: str = "openai_compatible"
    base_url: str
    capabilities: dict[str, Capability] = Field(default_factory=dict)
    input_modalities: list[InputModality] = Field(default_factory=list)
    output_modalities: list[OutputModality] = Field(default_factory=list)
    requires_authentication: bool = False
    authentication_method: str = "bearer_token"  # bearer_token, api_key, none
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_batching: bool = False
    health: ProviderHealth = ProviderHealth.UNKNOWN
    last_health_check: Optional[datetime] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    def model_dump(self, **kwargs):
        """Custom dump to handle enums and datetimes."""
        data = super().model_dump(**kwargs)
        # Convert enums to strings for JSON serialization
        if "kind" in data and hasattr(data["kind"], "value"):
            data["kind"] = data["kind"].value
        if "health" in data and hasattr(data["health"], "value"):
            data["health"] = data["health"].value
        # Convert datetime to ISO string
        if "last_health_check" in data and data["last_health_check"]:
            data["last_health_check"] = data["last_health_check"].isoformat()
        return data


# ─── Model Descriptor ───────────────────────────────────────────────────────

class ModelAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNVERIFIED = "unverified"


class ModelDescriptor(BaseModel):
    """Describes a specific model within a provider."""

    id: str  # provider-specific model ID
    provider_id: str
    display_name: str
    modalities: list[Modality] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)  # e.g. ["chat", "vision"]
    context_window: Optional[int] = None
    input_formats: list[str] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=list)
    streaming_supported: bool = False
    tool_calling_supported: bool = False
    vision_supported: bool = False
    embeddings_supported: bool = False
    availability: ModelAvailability = ModelAvailability.UNVERIFIED
    pricing: Optional[dict[str, float]] = None  # per-token costs if known
    quota_remaining: Optional[float] = None
    last_verified: Optional[datetime] = None
    metadata_source: str = "manual"  # manual, discovered, catalog
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─── Credential Model ───────────────────────────────────────────────────────

class CredentialStoreType(str, Enum):
    ENCRYPTED_FILE = "encrypted_file"
    SYSTEM_KEYRING = "system_keyring"
    ENVIRONMENT = "environment"


class Credential(BaseModel):
    """Secure storage for provider credentials."""

    id: str  # unique credential ID
    provider_id: str
    account_name: str  # human label for this credential set
    secret: SecretStr
    stored_at: datetime = Field(default_factory=datetime.utcnow)
    last_rotated: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def redact(self) -> str:
        """Return a safe representation without exposing the secret."""
        secret_str = self.secret.get_secret_value()
        if len(secret_str) <= 8:
            return f"***{len(secret_str)}***"
        return f"{secret_str[:4]}***{secret_str[-4:]}"

    @field_serializer("secret")
    def _serialize_secret(self, value: SecretStr, _info) -> str:
        return self.redact()


# ─── Request/Response Normalized Models ──────────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # "system", "user", "assistant", "tool"
    content: str | list[dict]  # text or multimodal content blocks
    name: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None


class NormalizedRequest(BaseModel):
    """Normalized internal request format."""

    model: str
    messages: list[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    tools: Optional[list[dict]] = None
    tool_choice: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = None


class NormalizedResponse(BaseModel):
    """Normalized internal response format."""

    id: str
    model: str
    choices: list[Choice]
    usage: Optional[dict[str, int]] = None
    created: int
    provider_id: str
    latency_ms: float
    routing_decision: Optional[dict[str, Any]] = None


class NormalizedError(BaseModel):
    """Normalized error response."""

    type: str
    message: str
    code: Optional[int] = None
    provider_id: Optional[str] = None
    original_error: Optional[str] = None  # should be redacted before logging
