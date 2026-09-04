"""OpenAI-compatible provider adapter.

This adapter handles providers that follow the OpenAI API convention,
including OpenAI itself, Nara Router, Ollama (with /v1 prefix), and
other compatible endpoints.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from .base import ProviderAdapterFactory, ProviderAdapter
from ..models import (
    ChatMessage,
    Credential,
    ModelDescriptor,
    NormalizedError,
    NormalizedRequest,
    NormalizedResponse,
    ProviderDescriptor,
    ProviderHealth,
)

logger = logging.getLogger(__name__)


@ProviderAdapterFactory.register("openai_compatible")
class OpenAICompatibleAdapter(ProviderAdapter):
    """Adapter for OpenAI-compatible APIs."""

    # Default Chat Completion endpoint
    CHAT_ENDPOINT = "/v1/chat/completions"
    MODELS_ENDPOINT = "/v1/models"

    def __init__(self, provider: ProviderDescriptor, credential_manager: Any):
        super().__init__(provider, credential_manager)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def chat_completion(
        self,
        request: NormalizedRequest,
        credential: Optional[Credential] = None,
        stream: bool = False,
    ) -> NormalizedResponse:
        """Execute a chat completion request.

        Args:
            request: Normalized request
            credential: Optional specific credential
            stream: Whether to stream

        Returns:
            NormalizedResponse

        Raises:
            Exception: If the request fails
        """
        client = await self._get_client()
        headers = self.get_auth_headers(credential)
        headers["Content-Type"] = "application/json"

        # Build provider-specific request
        provider_request = self._build_chat_request(request, stream)

        try:
            start_time = self._now_ms()
            response = await client.post(
                self.CHAT_ENDPOINT,
                json=provider_request,
                headers=headers,
                stream=stream,
            )
            elapsed_ms = self._now_ms() - start_time

            if response.status_code >= 400:
                error_body = await response.aread()
                error_msg = self._parse_error(response, error_body)
                self.set_health(ProviderHealth.UNAVAILABLE, error_msg)
                raise Exception(f"Provider error {response.status_code}: {error_msg}")

            if stream:
                # For streaming, return immediately and handle chunks via iterator
                return await self._handle_streaming_response(response, request, elapsed_ms)
            else:
                body = await response.aread()
                return self._parse_chat_response(json.loads(body), request, elapsed_ms)

        except httpx.HTTPStatusError as e:
            self.set_health(ProviderHealth.UNAVAILABLE, str(e))
            raise self.normalize_error(e) from e
        except httpx.RequestError as e:
            self.set_health(ProviderHealth.UNAVAILABLE, f"Connection error: {e}")
            raise self.normalize_error(e) from e

    async def list_models(self) -> list[ModelDescriptor]:
        """Discover available models.

        Returns:
            List of ModelDescriptor objects
        """
        client = await self._get_client()
        headers = self.get_auth_headers()
        headers["Content-Type"] = "application/json"

        try:
            response = await client.get(self.MODELS_ENDPOINT, headers=headers)
            if response.status_code != 200:
                logger.warning(f"Failed to list models: {response.status_code}")
                return []

            data = response.json()
            models = []
            for model_data in data.get("data", []):
                model = ModelDescriptor(
                    id=model_data.get("id", ""),
                    provider_id=self.provider.id,
                    display_name=model_data.get("id", ""),
                    capabilities=model_data.get("capabilities", []),
                    metadata=model_data,
                    metadata_source="discovered",
                )
                models.append(model)

            logger.info(f"Discovered {len(models)} models from {self.provider.id}")
            return models

        except Exception as e:
            logger.warning(f"Failed to discover models: {e}")
            return []

    async def health_check(self) -> tuple[bool, str]:
        """Check provider health.

        Returns:
            Tuple of (is_healthy, status_message)
        """
        client = await self._get_client()
        headers = self.get_auth_headers()
        headers["Content-Type"] = "application/json"

        try:
            # Try a simple models endpoint check
            response = await client.get(self.MODELS_ENDPOINT, headers=headers, timeout=5.0)
            if response.status_code == 200:
                self.set_health(ProviderHealth.HEALTHY, "Models endpoint accessible")
                return True, "Healthy"
            elif response.status_code == 401:
                self.set_health(ProviderHealth.AUTHENTICATION_FAILED, "Invalid credentials")
                return False, "Authentication failed"
            else:
                self.set_health(ProviderHealth.DEGRADED, f"Status: {response.status_code}")
                return False, f"Non-successful status: {response.status_code}"

        except httpx.RequestError as e:
            self.set_health(ProviderHealth.UNAVAILABLE, f"Connection failed: {e}")
            return False, f"Connection error: {e}"
        except Exception as e:
            self.set_health(ProviderHealth.UNKNOWN, str(e))
            return False, f"Health check error: {e}"

    def get_credential(self) -> Optional[Credential]:
        """Get credential for this provider."""
        return self.credential_manager.get_credential(self.provider.id)

    def _build_chat_request(self, request: NormalizedRequest, stream: bool) -> dict:
        """Convert normalized request to provider-specific format."""
        messages = []
        for msg in request.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content,
            })
            if msg.tool_calls:
                messages[-1]["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                messages[-1]["tool_call_id"] = msg.tool_call_id

        provider_request = {
            "model": request.model,
            "messages": messages,
            "stream": stream,
        }

        if request.temperature is not None:
            provider_request["temperature"] = request.temperature
        if request.max_tokens is not None:
            provider_request["max_tokens"] = request.max_tokens
        if request.tools:
            provider_request["tools"] = request.tools
        if request.tool_choice:
            provider_request["tool_choice"] = request.tool_choice

        return provider_request

    def _parse_chat_response(self, data: dict, request: NormalizedRequest, elapsed_ms: float) -> NormalizedResponse:
        """Parse provider response to normalized format."""
        choices = []
        for choice in data.get("choices", []):
            message_data = choice.get("message", {})
            message = ChatMessage(
                role=message_data.get("role", "assistant"),
                content=message_data.get("content", ""),
                tool_calls=message_data.get("tool_calls"),
            )
            choices.append(Choice(
                index=choice.get("index", 0),
                message=message,
                finish_reason=choice.get("finish_reason"),
            ))

        usage = data.get("usage")
        if usage:
            usage_dict = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        else:
            usage_dict = None

        return NormalizedResponse(
            id=data.get("id", ""),
            model=request.model,
            choices=choices,
            usage=usage_dict,
            created=data.get("created", int(self._now_ms() / 1000)),
            provider_id=self.provider.id,
            latency_ms=elapsed_ms,
        )

    async def _handle_streaming_response(
        self,
        response: httpx.Response,
        request: NormalizedRequest,
        elapsed_ms: float,
    ) -> NormalizedResponse:
        """Handle streaming response.

        Note: This is a simplified implementation. Full streaming support
        would require async iteration over chunks.
        """
        # Collect all chunks (simplified - in production, would stream)
        chunks = []
        async for chunk in response.aiter_lines():
            if chunk.startswith("data: "):
                data_str = chunk[6:].strip()
                if data_str != "[DONE]":
                    try:
                        chunk_data = json.loads(data_str)
                        chunks.append(chunk_data)
                    except json.JSONDecodeError:
                        continue

        if not chunks:
            return NormalizedResponse(
                id="",
                model=request.model,
                choices=[],
                usage=None,
                created=int(self._now_ms() / 1000),
                provider_id=self.provider.id,
                latency_ms=elapsed_ms,
            )

        # Combine chunks into final response
        final_message = ""
        total_tokens = 0
        for chunk in chunks:
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            if "content" in delta:
                final_message += delta["content"]
            usage = chunk.get("usage")
            if usage:
                total_tokens += usage.get("total_tokens", 0)

        choice = Choice(
            index=0,
            message=ChatMessage(role="assistant", content=final_message),
            finish_reason="stop",
        )

        return NormalizedResponse(
            id=chunks[0].get("id", "") if chunks else "",
            model=request.model,
            choices=[choice],
            usage={"total_tokens": total_tokens} if total_tokens else None,
            created=chunks[0].get("created", int(self._now_ms() / 1000)) if chunks else int(self._now_ms() / 1000),
            provider_id=self.provider.id,
            latency_ms=elapsed_ms,
        )

    def _parse_error(self, response: httpx.Response, body: bytes) -> str:
        """Parse error response from provider."""
        try:
            error_data = json.loads(body)
            error = error_data.get("error", {})
            return error.get("message", str(response.status_code))
        except json.JSONDecodeError:
            return body.decode('utf-8', errors='replace')[:200]

    @staticmethod
    def _now_ms() -> int:
        """Get current time in milliseconds."""
        import time
        return int(time.time() * 1000)


# Re-export Choice for external use
from ..models import Choice  # noqa: E402
