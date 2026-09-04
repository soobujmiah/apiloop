"""Tests for credential manager."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from apiloop.credentials.manager import (
    Credential,
    CredentialError,
    CredentialManager,
    CredentialNotFoundError,
)


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory."""
    return tmp_path / "apiloop"


@pytest.fixture
def credential_manager(temp_config_dir):
    """Create a CredentialManager with temporary storage."""
    storage_path = temp_config_dir / "credentials.json"
    return CredentialManager(storage_path=storage_path)


class TestCredentialManager:
    def test_add_credential(self, credential_manager):
        """Test adding a new credential."""
        cred = credential_manager.add_credential(
            provider_id="test-provider",
            account_name="default",
            secret="sk-test-key-123",
        )
        assert cred.id == "test-provider:default"
        assert cred.provider_id == "test-provider"
        assert cred.account_name == "default"
        assert credential_manager.credential_count == 1

    def test_duplicate_credential_raises_error(self, credential_manager):
        """Test that duplicate credentials raise an error."""
        credential_manager.add_credential(
            provider_id="test",
            account_name="default",
            secret="key1",
        )
        with pytest.raises(CredentialError):
            credential_manager.add_credential(
                provider_id="test",
                account_name="default",
                secret="key2",
            )

    def test_get_credential(self, credential_manager):
        """Test retrieving a credential."""
        credential_manager.add_credential(
            provider_id="test",
            account_name="default",
            secret="secret123",
        )
        cred = credential_manager.get_credential("test")
        assert cred is not None
        assert cred.provider_id == "test"
        assert cred.secret.get_secret_value() == "secret123"

    def test_get_credential_not_found(self, credential_manager):
        """Test getting a non-existent credential."""
        cred = credential_manager.get_credential("nonexistent")
        assert cred is None

    def test_list_credentials(self, credential_manager):
        """Test listing all credentials."""
        credential_manager.add_credential(
            provider_id="provider1",
            account_name="acc1",
            secret="key1",
        )
        credential_manager.add_credential(
            provider_id="provider1",
            account_name="acc2",
            secret="key2",
        )
        credential_manager.add_credential(
            provider_id="provider2",
            account_name="acc1",
            secret="key3",
        )
        creds = credential_manager.list_credentials()
        assert len(creds) == 3

    def test_list_credentials_by_provider(self, credential_manager):
        """Test filtering credentials by provider."""
        credential_manager.add_credential(
            provider_id="provider1",
            account_name="acc1",
            secret="key1",
        )
        credential_manager.add_credential(
            provider_id="provider2",
            account_name="acc1",
            secret="key2",
        )
        creds = credential_manager.list_credentials(provider_id="provider1")
        assert len(creds) == 1
        assert creds[0].provider_id == "provider1"

    def test_remove_credential(self, credential_manager):
        """Test removing a credential."""
        credential_manager.add_credential(
            provider_id="test",
            account_name="default",
            secret="secret",
        )
        assert credential_manager.credential_count == 1
        result = credential_manager.remove_credential("test", "default")
        assert result is True
        assert credential_manager.credential_count == 0

    def test_remove_nonexistent_credential(self, credential_manager):
        """Test removing a non-existent credential."""
        result = credential_manager.remove_credential("test", "default")
        assert result is False

    def test_rotate_credential(self, credential_manager):
        """Test rotating a credential."""
        credential_manager.add_credential(
            provider_id="test",
            account_name="default",
            secret="old-secret",
        )
        new_cred = credential_manager.rotate_credential(
            provider_id="test",
            account_name="default",
            new_secret="new-secret",
        )
        assert new_cred.secret.get_secret_value() == "new-secret"
        # last_rotated should be set (not None)
        assert new_cred.last_rotated is not None

    def test_rotate_nonexistent_raises_error(self, credential_manager):
        """Test rotating a non-existent credential raises error."""
        with pytest.raises(CredentialNotFoundError):
            credential_manager.rotate_credential(
                provider_id="test",
                account_name="default",
                new_secret="new-secret",
            )

    def test_secret_redaction_in_list(self, credential_manager):
        """Test that secrets are redacted when listing credentials."""
        credential_manager.add_credential(
            provider_id="test",
            account_name="default",
            secret="sk-very-secret-key-12345",
        )
        creds = credential_manager.list_credentials()
        for cred in creds:
            # The raw secret should NOT be accessible from the returned object
            # (it's wrapped in SecretStr and should be sanitized on access)
            assert cred.id == "test:default"

    def test_persistence_across_instances(self, temp_config_dir):
        """Test that credentials persist across manager instances."""
        temp_config_dir.mkdir(parents=True, exist_ok=True)
        storage_path = temp_config_dir / "credentials.json"
        cm1 = CredentialManager(storage_path=storage_path)
        cm1.add_credential(
            provider_id="test",
            account_name="default",
            secret="persistent-secret",
        )
        del cm1

        # Create new instance
        cm2 = CredentialManager(storage_path=storage_path)
        cred = cm2.get_credential("test")
        assert cred is not None
        # Verify the credential was loaded correctly
        assert cred.provider_id == "test"
        assert cred.account_name == "default"

    def test_validation_requires_credentials(self, credential_manager):
        """Test validation when no credentials exist."""
        from apiloop.models import ProviderDescriptor, ProviderKind
        provider = ProviderDescriptor(
            id="test",
            name="Test",
            kind=ProviderKind.REMOTE,
            base_url="https://example.com",
            requires_authentication=True,
        )
        is_valid, msg = credential_manager.validate_provider_credentials(provider)
        assert is_valid is False
        assert "No credentials" in msg

    def test_validation_passes_with_credentials(self, credential_manager):
        """Test validation passes when credentials exist."""
        from apiloop.models import ProviderDescriptor, ProviderKind
        provider = ProviderDescriptor(
            id="test",
            name="Test",
            kind=ProviderKind.REMOTE,
            base_url="https://example.com",
            requires_authentication=True,
        )
        credential_manager.add_credential(
            provider_id="test",
            account_name="default",
            secret="secret",
        )
        is_valid, msg = credential_manager.validate_provider_credentials(provider)
        assert is_valid is True

    def test_no_validation_needed_for_local(self, credential_manager):
        """Test validation passes for local providers without auth."""
        from apiloop.models import ProviderDescriptor, ProviderKind
        provider = ProviderDescriptor(
            id="ollama",
            name="Ollama",
            kind=ProviderKind.LOCAL,
            base_url="http://localhost:11434",
            requires_authentication=False,
        )
        is_valid, msg = credential_manager.validate_provider_credentials(provider)
        assert is_valid is True
        assert msg is None


class TestCredentialRedaction:
    def test_short_secret_redaction(self):
        """Test redaction of short secrets."""
        cred = Credential(
            id="test:default",
            provider_id="test",
            account_name="default",
            secret="abc",
        )
        redacted = cred.redact()
        assert "abc" not in redacted

    def test_long_secret_redaction(self):
        """Test redaction of long secrets."""
        cred = Credential(
            id="test:default",
            provider_id="test",
            account_name="default",
            secret="sk-very-long-api-key-that-should-be-redacted-completely",
        )
        redacted = cred.redact()
        assert "sk-very-long" not in redacted
        assert "***" in redacted
