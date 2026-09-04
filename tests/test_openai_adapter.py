"""Tests for OpenAI-compatible adapter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json

from apiloop.providers.openai_compatible import OpenAICompatibleAdapter
from apiloop.models import (
    ChatMessage,
    NormalizedRequest,
    NormalizedResponse,
    ProviderDescriptor,
    ProviderHealth,
    ProviderKind,
)


@pytest.fixture
def mock_provider():
    """Create a mock provider descriptor."""
    return ProviderDescriptor(
        id="test-openai",
        name="Test OpenAI",
        kind=ProviderKind.REMOTE,
        base_url="https://api.test.com/v1",
        requires_authentication=True,
        authentication_method="bearer_token",
        supports_streaming=True,
    )


@pytest.fixture
def mock_credential_manager():
    """Create a mock credential manager."""
    manager = MagicMock()
    manager.get_credential.return_value = MagicMock(
        secret=MagicMock(get_secret_value=lambda: "test-api-key"),
        redact=lambda: "sk-te***key",
    )
    return manager


@pytest.fixture
def adapter(mock_provider, mock_credential_manager):
    """Create an adapter instance."""
    return OpenAICompatibleAdapter(mock_provider, mock_credential_manager)


class TestOpenAICompatibleAdapter:
    @pytest.mark.asyncio
    async def test_chat_completion_success(self, adapter):
        """Test successful chat completion."""
        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aread = AsyncMock(return_value=json.dumps({
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode())
        mock_response.is_closed = True

        with patch('apiloop.providers.openai_compatible.httpx.AsyncClient') as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            request = NormalizedRequest(
                model="gpt-4",
                messages=[ChatMessage(role="user", content="Hello")],
            )
            response = await adapter.chat_completion(request)
            assert response.id == "chatcmpl-123"
            assert len(response.choices) == 1
            assert response.choices[0].message.content == "Hello!"

    @pytest.mark.asyncio
    async def test_chat_completion_auth_error(self, adapter):
        """Test authentication error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.aread = AsyncMock(return_value=json.dumps({
            "error": {"type": "authentication_error", "message": "Invalid API key"}
        }).encode())

        with patch('apiloop.providers.openai_compatible.httpx.AsyncClient') as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            request = NormalizedRequest(
                model="gpt-4",
                messages=[ChatMessage(role="user", content="Hello")],
            )
            with pytest.raises(Exception):
                await adapter.chat_completion(request)

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, adapter):
        """Test health check returns healthy."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}

        with patch('apiloop.providers.openai_compatible.httpx.AsyncClient') as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            healthy, msg = await adapter.health_check()
            assert healthy is True

    @pytest.mark.asyncio
    async def test_health_check_auth_failed(self, adapter):
        """Test health check detects auth failure."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch('apiloop.providers.openai_compatible.httpx.AsyncClient') as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            healthy, msg = await adapter.health_check()
            assert healthy is False

    @pytest.mark.asyncio
    async def test_list_models(self, adapter):
        """Test model discovery."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "gpt-4", "capabilities": ["chat"]},
                {"id": "gpt-3.5-turbo", "capabilities": ["chat"]},
            ]
        }

        with patch('apiloop.providers.openai_compatible.httpx.AsyncClient') as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            models = await adapter.list_models()
            assert len(models) == 2
            assert models[0].id == "gpt-4"
            assert models[0].provider_id == "test-openai"

    def test_get_auth_headers_bearer(self, adapter):
        """Test bearer token header generation."""
        headers = adapter.get_auth_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert "test-api-key" in headers["Authorization"]

    def test_normalize_error(self, adapter):
        """Test error normalization."""
        error = adapter.normalize_error("Some error occurred")
        assert error.type == "provider_error"
        assert "Some error" in error.message

    def test_redact_secrets_static(self):
        """Test secret redaction utility."""
        text = "Using Bearer sk-test-key-12345 for auth"
        redacted = OpenAICompatibleAdapter._redact_secrets(text)
        assert "sk-test-key-12345" not in redacted
        assert "Bearer ***REDACTED***" in redacted
