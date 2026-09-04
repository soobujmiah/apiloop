"""Provider adapter base class and registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from ..models import (
    Credential,
    ModelDescriptor,
    NormalizedError,
    NormalizedRequest,
    NormalizedResponse,
    ProviderDescriptor,
)

logger = logging.getLogger(__name__)


class ProviderAdapter(ABC):
    """Base class for all provider adapters.

    Each provider (OpenAI, Anthropic, Ollama, etc.) implements this interface
    to translate between APIloop's normalized format and the provider's native
    API format.
    """

    def __init__(self, provider: ProviderDescriptor, credential_manager: Any):
        """Initialize the adapter.

        Args:
            provider: Provider descriptor
            credential_manager: CredentialManager instance
        """
        self.provider = provider
        self.credential_manager = credential_manager
        self._base_url = provider.base_url.rstrip("/")
        self._health = provider.health

    @property
    def provider_id(self) -> str:
        """Get the provider identifier."""
        return self.provider.id

    @property
    def health(self) -> str:
        """Get current health status."""
        return self._health

    @abstractmethod
    async def chat_completion(
        self,
        request: NormalizedRequest,
        credential: Optional[Credential] = None,
        stream: bool = False,
    ) -> NormalizedResponse:
        """Execute a chat completion request.

        Args:
            request: Normalized request
            credential: Optional specific credential to use
            stream: Whether to stream the response

        Returns:
            NormalizedResponse

        Raises:
            ProviderError: If the provider call fails
        """
        ...

    @abstractmethod
    async def list_models(self) -> list[ModelDescriptor]:
        """Discover available models from the provider.

        Returns:
            List of ModelDescriptor objects
        """
        ...

    @abstractmethod
    async def health_check(self) -> tuple[bool, str]:
        """Check provider health and connectivity.

        Returns:
            Tuple of (is_healthy, status_message)
        """
        ...

    @abstractmethod
    def get_credential(self) -> Optional[Credential]:
        """Get the credential for this provider.

        Returns:
            Credential if configured, None otherwise
        """
        ...

    def get_auth_headers(self, credential: Optional[Credential] = None) -> dict[str, str]:
        """Build authentication headers.

        Args:
            credential: Optional credential override

        Returns:
            Headers dictionary with authentication
        """
        cred = credential or self.get_credential()
        if not cred or not self.provider.requires_authentication:
            return {}

        secret = cred.secret.get_secret_value()
        method = self.provider.authentication_method

        if method == "bearer_token":
            return {"Authorization": f"Bearer {secret}"}
        elif method == "api_key":
            return {"x-api-key": secret}
        else:
            logger.warning(f"Unknown auth method: {method}")
            return {}

    def normalize_error(self, error: Any) -> NormalizedError:
        """Convert a provider-specific error to a normalized error.

        Args:
            error: Provider-specific error object or message

        Returns:
            NormalizedError with redacted sensitive information
        """
        if isinstance(error, NormalizedError):
            return error

        message = str(error)
        # Redact potential API keys in error messages
        message = self._redact_secrets(message)

        return NormalizedError(
            type="provider_error",
            message=message,
            provider_id=self.provider.id,
            original_error=message,
        )

    @staticmethod
    def _redact_secrets(text: str) -> str:
        """Redact potential secrets from text.

        Args:
            text: Input text

        Returns:
            Text with secrets redacted
        """
        import re
        # Redact bearer tokens
        text = re.sub(r'Bearer\s+[a-zA-Z0-9\-_.~+/]+=*', 'Bearer ***REDACTED***', text)
        # Redact API keys (common patterns)
        text = re.sub(r'(api[_-]?key["\s:=]+)[a-zA-Z0-9\-_.~+/]{8,}', r'\1***REDACTED***', text, flags=re.IGNORECASE)
        return text

    def set_health(self, health: str, message: Optional[str] = None) -> None:
        """Update health status.

        Args:
            health: New health status
            message: Optional status message
        """
        self._health = health
        self.provider.health = health
        if message:
            self.provider.error_message = message


class ProviderAdapterFactory:
    """Factory for creating provider adapters."""

    _registry: dict[str, type[ProviderAdapter]] = {}

    @classmethod
    def register(cls, provider_id: str):
        """Decorator to register a provider adapter class."""
        def decorator(adapter_class: type[ProviderAdapter]) -> type[ProviderAdapter]:
            cls._registry[provider_id] = adapter_class
            logger.info(f"Registered provider adapter: {provider_id}")
            return adapter_class
        return decorator

    @classmethod
    def create(cls, provider: ProviderDescriptor, credential_manager: Any) -> ProviderAdapter:
        """Create an adapter for a provider.

        Args:
            provider: Provider descriptor
            credential_manager: CredentialManager instance

        Returns:
            ProviderAdapter instance

        Raises:
            ValueError: If no adapter is registered for the provider
        """
        adapter_class = cls._registry.get(provider.adapter_type)
        if not adapter_class:
            # Try to use generic OpenAI-compatible adapter as fallback
            # Import here to avoid circular imports
            from .openai_compatible import OpenAICompatibleAdapter
            adapter_class = OpenAICompatibleAdapter

        return adapter_class(provider, credential_manager)

    @classmethod
    def list_providers(cls) -> list[str]:
        """List all registered provider IDs."""
        return list(cls._registry.keys())
