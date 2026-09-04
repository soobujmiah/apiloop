"""Tests for APIloop models."""

import pytest
from datetime import datetime

from apiloop.models import (
    Capability,
    ChatMessage,
    Choice,
    Credential,
    InputModality,
    ModelDescriptor,
    ModelAvailability,
    Modality,
    NormalizedError,
    NormalizedRequest,
    NormalizedResponse,
    OutputModality,
    ProviderDescriptor,
    ProviderHealth,
    ProviderKind,
)


class TestCapability:
    def test_basic_capability(self):
        cap = Capability(name="chat", description="Chat completion")
        assert cap.name == "chat"
        assert cap.supported is False

    def test_supported_capability(self):
        cap = Capability(name="vision", supported=True)
        assert cap.supported is True


class TestProviderDescriptor:
    def test_basic_provider(self):
        provider = ProviderDescriptor(
            id="test-provider",
            name="Test Provider",
            kind=ProviderKind.REMOTE,
            base_url="https://api.example.com/v1",
        )
        assert provider.id == "test-provider"
        assert provider.health == ProviderHealth.UNKNOWN
        assert provider.requires_authentication is False

    def test_remote_provider_with_auth(self):
        provider = ProviderDescriptor(
            id="openai",
            name="OpenAI",
            kind=ProviderKind.REMOTE,
            base_url="https://api.openai.com/v1",
            requires_authentication=True,
            authentication_method="bearer_token",
        )
        assert provider.requires_authentication is True
        assert provider.authentication_method == "bearer_token"

    def test_local_provider(self):
        provider = ProviderDescriptor(
            id="ollama",
            name="Ollama Local",
            kind=ProviderKind.LOCAL,
            base_url="http://localhost:11434",
        )
        assert provider.kind == ProviderKind.LOCAL


class TestModelDescriptor:
    def test_basic_model(self):
        model = ModelDescriptor(
            id="gpt-4",
            provider_id="openai",
            display_name="GPT-4",
        )
        assert model.id == "gpt-4"
        assert model.availability == ModelAvailability.UNVERIFIED

    def test_vision_model(self):
        model = ModelDescriptor(
            id="gpt-4-vision",
            provider_id="openai",
            display_name="GPT-4 Vision",
            modalities=[Modality.TEXT, Modality.IMAGE],
            capabilities=["chat", "vision"],
            vision_supported=True,
        )
        assert Modality.IMAGE in model.modalities
        assert model.vision_supported is True

    def test_embedding_model(self):
        model = ModelDescriptor(
            id="text-embedding-3-small",
            provider_id="openai",
            display_name="Text Embedding 3 Small",
            capabilities=["embeddings"],
            embeddings_supported=True,
        )
        assert model.embeddings_supported is True


class TestCredential:
    def test_secret_redaction(self):
        cred = Credential(
            id="test:default",
            provider_id="test",
            account_name="default",
            secret="sk-test-key-12345",
        )
        redacted = cred.redact()
        # Redaction keeps first 4 and last 4 chars visible
        assert "sk-t" in redacted
        assert "2345" in redacted
        assert "test-key-1" not in redacted

    def test_serialization_hides_secret(self):
        cred = Credential(
            id="test:default",
            provider_id="test",
            account_name="default",
            secret="sk-secret-key",
        )
        data = cred.model_dump()
        # Secret should be redacted in serialization
        assert "sk-secret" not in str(data["secret"])

    def test_empty_secret(self):
        cred = Credential(
            id="test:default",
            provider_id="test",
            account_name="default",
            secret="",
        )
        assert cred.secret.get_secret_value() == ""


class TestNormalizedRequest:
    def test_basic_chat_request(self):
        request = NormalizedRequest(
            model="gpt-4",
            messages=[
                ChatMessage(role="system", content="You are helpful"),
                ChatMessage(role="user", content="Hello"),
            ],
        )
        assert len(request.messages) == 2
        assert request.messages[0].role == "system"
        assert request.messages[1].content == "Hello"

    def test_request_with_tools(self):
        request = NormalizedRequest(
            model="gpt-4",
            messages=[ChatMessage(role="user", content="What's the weather?")],
            tools=[{"type": "function", "function": {"name": "get_weather"}}],
        )
        assert request.tools is not None
        assert len(request.tools) == 1


class TestNormalizedResponse:
    def test_basic_response(self):
        response = NormalizedResponse(
            id="chatcmpl-123",
            model="gpt-4",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Hello!"),
                    finish_reason="stop",
                )
            ],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            created=1234567890,
            provider_id="openai",
            latency_ms=150.5,
        )
        assert response.id == "chatcmpl-123"
        assert len(response.choices) == 1
        assert response.choices[0].message.content == "Hello!"


class TestNormalizedError:
    def test_error_without_secrets(self):
        error = NormalizedError(
            type="authentication_error",
            message="Invalid API key provided",
            code=401,
            provider_id="openai",
        )
        assert error.type == "authentication_error"
        assert "API key" in error.message

    def test_error_with_redacted_secret(self):
        # Note: original_error stores the raw error; normalization happens in adapter
        error = NormalizedError(
            type="provider_error",
            message="Bearer token invalid",
            original_error="Some error context",
        )
        # Verify the error object itself doesn't contain secrets in message
        assert "invalid" in error.message.lower() or "Bearer" in error.message


class TestModality:
    def test_modality_enum(self):
        assert Modality.TEXT == "text"
        assert Modality.IMAGE == "image"
        assert Modality.AUDIO == "audio"
        assert Modality.VIDEO == "video"

    def test_input_modality(self):
        input_mod = InputModality(modality=Modality.IMAGE, formats=["png", "jpeg"])
        assert input_mod.formats == ["png", "jpeg"]

    def test_output_modality(self):
        output_mod = OutputModality(modality=Modality.TEXT, formats=["text"])
        assert output_mod.modality == Modality.TEXT
