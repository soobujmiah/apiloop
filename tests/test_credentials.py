"""Tests for credential manager."""

import json
import multiprocessing
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from apiloop.credentials.manager import (
    Credential,
    CredentialEncryptionError,
    CredentialError,
    CredentialManager,
    CredentialNotFoundError,
)


def _add_credential_worker(storage_path, idx):
    """Module-level (picklable) helper for multiprocessing-based concurrency tests."""
    cm = CredentialManager(storage_path=storage_path)
    cm.add_credential(provider_id=f"concurrent-p{idx}", account_name="acct", secret=f"secret-{idx}")


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
        assert cred.secret.get_secret_value() == "persistent-secret"

    def test_secret_value_survives_save_and_reload(self, temp_config_dir):
        """Regression: _save_credentials() built its payload via
        cred.model_dump(), which redacts the `secret` field (correct for
        display/logging - see Credential's field_serializer - but that
        serializer was being reused for the encrypted store's own internal
        persistence too). Every credential's real secret was silently
        replaced with an unusable redacted stub (e.g. "sk-t***2345") the
        moment it was saved, recoverable only in the same in-memory
        instance that added it - a fresh load, or this manager's own
        resync-before-mutate, would load garbage instead of the real key.
        """
        storage_path = temp_config_dir / "credentials.json"
        cm1 = CredentialManager(storage_path=storage_path)
        cm1.add_credential(provider_id="test", account_name="default", secret="sk-real-usable-secret-value")
        del cm1

        cm2 = CredentialManager(storage_path=storage_path)
        assert cm2.get_secret_value("test", "default") == "sk-real-usable-secret-value"

    def test_secret_value_survives_a_second_add_triggering_resync(self, temp_config_dir):
        """A second mutation resyncs from disk before writing (see the N8
        fix) - that reload must not corrupt the first credential's secret."""
        storage_path = temp_config_dir / "credentials.json"
        cm = CredentialManager(storage_path=storage_path)
        cm.add_credential(provider_id="p1", account_name="a", secret="sk-first-secret")
        cm.add_credential(provider_id="p2", account_name="b", secret="sk-second-secret")

        assert cm.get_secret_value("p1", "a") == "sk-first-secret"
        assert cm.get_secret_value("p2", "b") == "sk-second-secret"

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


class TestCredentialConcurrency:
    """Concurrent/uncoordinated writers must not silently lose credentials.

    Regression coverage for a real bug: two CredentialManager instances
    (e.g. two CLI invocations, or a CLI run overlapping the gateway
    process) each held their own in-memory snapshot and did a blind
    whole-file overwrite on save, so the second writer silently deleted
    whatever the first had just persisted.
    """

    def test_two_instances_no_lost_update(self, temp_config_dir):
        storage_path = temp_config_dir / "credentials.json"
        cm_a = CredentialManager(storage_path=storage_path)
        cm_b = CredentialManager(storage_path=storage_path)

        cm_a.add_credential(provider_id="p1", account_name="a", secret="secret-a")
        cm_b.add_credential(provider_id="p2", account_name="b", secret="secret-b")

        cm_c = CredentialManager(storage_path=storage_path)
        ids = {c.id for c in cm_c.list_credentials()}
        assert ids == {"p1:a", "p2:b"}

    def test_concurrent_os_processes_no_lost_update(self, temp_config_dir):
        storage_path = temp_config_dir / "credentials.json"
        CredentialManager(storage_path=storage_path)  # prime the master key first

        n = 6
        procs = [
            multiprocessing.Process(target=_add_credential_worker, args=(storage_path, i))
            for i in range(n)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
            assert p.exitcode == 0

        cm = CredentialManager(storage_path=storage_path)
        ids = {c.id for c in cm.list_credentials()}
        assert ids == {f"concurrent-p{i}:acct" for i in range(n)}

    def test_removed_credential_does_not_reappear_after_stale_reload(self, temp_config_dir):
        """_load_credentials() must replace, not merge into, in-memory state -
        otherwise a credential removed by another instance would linger."""
        storage_path = temp_config_dir / "credentials.json"
        cm_a = CredentialManager(storage_path=storage_path)
        cm_a.add_credential(provider_id="p1", account_name="a", secret="secret-a")

        cm_b = CredentialManager(storage_path=storage_path)
        cm_b.remove_credential("p1", "a")

        # cm_a still has a stale in-memory copy of p1:a; a subsequent mutation
        # (which resyncs from disk first) must not resurrect it.
        cm_a.add_credential(provider_id="p2", account_name="b", secret="secret-b")
        ids = {c.id for c in cm_a.list_credentials()}
        assert ids == {"p2:b"}


class TestCorruptedStore:
    def test_corrupted_store_raises_encryption_error_not_nameerror(self, temp_config_dir):
        """Regression: `except InvalidToken:` (no `as e`) meant `from e`
        raised NameError instead of the intended CredentialEncryptionError,
        so a corrupted store crashed with a confusing internal error
        instead of a catchable, documented exception."""
        storage_path = temp_config_dir / "credentials.json"
        CredentialManager(storage_path=storage_path)  # creates the master key

        storage_path.write_bytes(b"gAAAAABnotavalidfernettoken" + b"x" * 60)

        with pytest.raises(CredentialEncryptionError):
            CredentialManager(storage_path=storage_path)


class TestMasterKeyRotation:
    def test_rotation_changes_key_file_contents(self, temp_config_dir):
        storage_path = temp_config_dir / "credentials.json"
        key_path = temp_config_dir / ".master_key"
        cm = CredentialManager(storage_path=storage_path)
        old_key = key_path.read_bytes()

        cm.rotate_master_key()

        assert key_path.read_bytes() != old_key

    def test_rotation_preserves_all_credentials(self, temp_config_dir):
        storage_path = temp_config_dir / "credentials.json"
        cm = CredentialManager(storage_path=storage_path)
        cm.add_credential(provider_id="p1", account_name="a", secret="secret-a")
        cm.add_credential(provider_id="p2", account_name="b", secret="secret-b")

        cm.rotate_master_key()

        cm2 = CredentialManager(storage_path=storage_path)
        assert cm2.get_secret_value("p1", "a") == "secret-a"
        assert cm2.get_secret_value("p2", "b") == "secret-b"

    def test_rotation_actually_re_encrypts_not_just_renames_key(self, temp_config_dir):
        """The old key must no longer decrypt the store after rotation -
        otherwise this would just be theater, not real re-keying."""
        from cryptography.fernet import Fernet, InvalidToken

        storage_path = temp_config_dir / "credentials.json"
        key_path = temp_config_dir / ".master_key"
        cm = CredentialManager(storage_path=storage_path)
        cm.add_credential(provider_id="p1", account_name="a", secret="secret-a")
        old_key = key_path.read_bytes()

        cm.rotate_master_key()

        old_fernet = Fernet(old_key)
        with pytest.raises(InvalidToken):
            old_fernet.decrypt(storage_path.read_bytes())

    def test_rotation_cleans_up_backup_on_success(self, temp_config_dir):
        storage_path = temp_config_dir / "credentials.json"
        backup_path = temp_config_dir / ".master_key.previous"
        cm = CredentialManager(storage_path=storage_path)

        cm.rotate_master_key()

        assert not backup_path.exists()

    def test_rotation_rolls_back_on_failure(self, temp_config_dir):
        """If re-encryption fails partway, the key file and in-memory
        Fernet instance must be restored so the store stays decryptable
        with the credentials it already had - not left in a broken,
        half-migrated state."""
        storage_path = temp_config_dir / "credentials.json"
        key_path = temp_config_dir / ".master_key"
        cm = CredentialManager(storage_path=storage_path)
        cm.add_credential(provider_id="p1", account_name="a", secret="secret-a")
        old_key = key_path.read_bytes()

        with patch.object(CredentialManager, "_save_credentials", side_effect=OSError("disk full (simulated)")):
            with pytest.raises(OSError):
                cm.rotate_master_key()

        # Key file rolled back to the original key...
        assert key_path.read_bytes() == old_key
        # ...and the store is still readable with it, credential intact.
        cm2 = CredentialManager(storage_path=storage_path)
        assert cm2.get_secret_value("p1", "a") == "secret-a"
