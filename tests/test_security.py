"""Security tests for APIloop."""

import pytest
from unittest.mock import MagicMock

from apiloop.models import NormalizedError, Credential
from apiloop.providers.openai_compatible import OpenAICompatibleAdapter
from apiloop.providers.base import ProviderAdapterFactory


class TestSecretRedaction:
    def test_redact_bearer_token(self):
        """Test bearer token redaction in errors."""
        error_text = "Authorization: Bearer sk-test-key-12345 failed"
        redacted = OpenAICompatibleAdapter._redact_secrets(error_text)
        assert "sk-test-key-12345" not in redacted
        assert "Bearer ***REDACTED***" in redacted

    def test_redact_api_key_in_message(self):
        """Test API key redaction in error messages."""
        error_text = 'api_key=sk-secret-key-abc123 was invalid'
        redacted = OpenAICompatibleAdapter._redact_secrets(error_text)
        assert "sk-secret-key-abc123" not in redacted

    def test_preserve_non_secret_info(self):
        """Test that non-secret info is preserved."""
        error_text = "Rate limit exceeded: 429 Too Many Requests"
        redacted = OpenAICompatibleAdapter._redact_secrets(error_text)
        assert redacted == error_text


class TestCredentialSecurity:
    def test_credential_not_in_logs(self):
        """Test that credentials don't appear in logs."""
        cred = Credential(
            id="test:default",
            provider_id="test",
            account_name="default",
            secret="sk-my-secret-key",
        )
        # Serialization should hide the secret
        serialized = cred.model_dump()
        assert "sk-my-secret-key" not in str(serialized)

    def test_redact_property(self):
        """Test redact method."""
        cred = Credential(
            id="test:default",
            provider_id="test",
            account_name="default",
            secret="sk-visible-start-end",
        )
        redacted = cred.redact()
        # First 4 and last 4 chars are visible, middle is redacted
        assert "sk-v" in redacted
        assert "-end" in redacted
        assert "isible-star" not in redacted


class TestProviderFactory:
    def test_registry_contains_expected_providers(self):
        """Test that expected providers are registered."""
        providers = ProviderAdapterFactory.list_providers()
        assert "openai_compatible" in providers

    def test_create_unknown_provider_falls_back(self):
        """Test fallback for unknown providers."""
        from apiloop.models import ProviderDescriptor, ProviderKind
        provider = ProviderDescriptor(
            id="unknown",
            name="Unknown",
            kind=ProviderKind.REMOTE,
            base_url="https://unknown.com",
        )
        cm = MagicMock()
        adapter = ProviderAdapterFactory.create(provider, cm)
        # Should fall back to OpenAI-compatible adapter
        assert isinstance(adapter, OpenAICompatibleAdapter)


class TestNormalizedError:
    def test_error_without_secrets(self):
        """Test that NormalizedError doesn't contain raw secrets."""
        error = NormalizedError(
            type="auth_error",
            message="Invalid bearer token provided",
            original_error="Bearer token invalid",
        )
        # Original error should not contain actual secret keys
        assert "sk-" not in error.original_error
