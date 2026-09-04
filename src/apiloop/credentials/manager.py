"""Secure credential management for APIloop.

Handles credential storage, retrieval, rotation, and redaction.
Supports multiple backends: encrypted file, system keyring, environment variables.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from ..models import Credential, ProviderDescriptor

logger = logging.getLogger(__name__)


class CredentialError(Exception):
    """Base exception for credential operations."""


class CredentialNotFoundError(CredentialError):
    """Raised when a credential is not found."""


class CredentialEncryptionError(CredentialError):
    """Raised when credential encryption/decryption fails."""


class CredentialManager:
    """Manages secure storage and retrieval of provider credentials."""

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize the credential manager.

        Args:
            storage_path: Path to store encrypted credentials. Defaults to
                         ~/.config/apiloop/credentials.json.
        """
        self._storage_path = storage_path or self._default_storage_path()
        # Ensure parent directory exists
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._storage_path.with_name(self._storage_path.name + ".lock")
        self._fernet: Optional[Fernet] = None
        self._credentials: dict[str, Credential] = {}
        self._load_master_key()
        self._load_credentials()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize the read-modify-write cycle of a mutation across
        processes/instances via an advisory file lock, so a concurrent
        writer's change is never silently overwritten by a stale in-memory
        snapshot (see: credential loss under concurrent CLI/gateway use).
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _default_storage_path() -> Path:
        """Get default storage path in user's config directory."""
        config_dir = Path.home() / ".config" / "apiloop"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "credentials.json"

    @property
    def _key_path(self) -> Path:
        return self._storage_path.parent / ".master_key"

    @staticmethod
    def _write_key_atomic(key_path: Path, key: bytes) -> None:
        """Write a key file via temp file + rename, so a crash mid-write
        can't leave a truncated/corrupt key in place."""
        fd, tmp_name = tempfile.mkstemp(
            dir=str(key_path.parent), prefix=".master_key-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(key)
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp_name, key_path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def _load_master_key(self) -> None:
        """Load or generate the master encryption key."""
        key_path = self._key_path

        if key_path.exists():
            try:
                key = key_path.read_bytes().strip()
                self._fernet = Fernet(key)
            except Exception as e:
                logger.error(f"Failed to load master key: {e}")
                raise CredentialEncryptionError("Failed to load master key") from e
        else:
            # Generate new master key
            key = Fernet.generate_key()
            self._write_key_atomic(key_path, key)
            self._fernet = Fernet(key)
            logger.info("Generated new master encryption key")

    def _load_credentials(self) -> None:
        """Load encrypted credentials from storage."""
        if not self._storage_path.exists():
            return

        try:
            encrypted_data = self._storage_path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted_data).decode()
            data = json.loads(decrypted)

            # Replace, don't merge: this method is also called to resync
            # with disk before a mutation, and a credential removed by
            # another process/instance in the meantime must disappear here
            # too, not linger from a stale in-memory copy.
            self._credentials = {}
            for cred_data in data.get("credentials", []):
                # Convert ISO format strings back to datetime
                if "stored_at" in cred_data and isinstance(cred_data["stored_at"], str):
                    cred_data["stored_at"] = datetime.fromisoformat(cred_data["stored_at"])
                if "last_rotated" in cred_data and isinstance(cred_data["last_rotated"], str):
                    cred_data["last_rotated"] = datetime.fromisoformat(cred_data["last_rotated"])
                    # Handle None value
                    if cred_data["last_rotated"] == "None":
                        cred_data["last_rotated"] = None

                cred = Credential(**cred_data)
                self._credentials[cred.id] = cred

            logger.debug(f"Loaded {len(self._credentials)} credentials")
        except InvalidToken as e:
            logger.error("Failed to decrypt credentials - possible key corruption")
            raise CredentialEncryptionError("Credential decryption failed") from e
        except Exception as e:
            logger.warning(f"Failed to load credentials: {e}")
            self._credentials = {}

    def _save_credentials(self) -> None:
        """Persist encrypted credentials to storage."""
        # Ensure parent directory exists
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        # Serialize with datetime handling
        credentials_data = []
        for cred in self._credentials.values():
            data = cred.model_dump()
            # cred.model_dump() redacts `secret` (via Credential's
            # field_serializer) - correct for display/logging, but this
            # payload is the encrypted store's own internal persistence:
            # it must hold the real, recoverable secret, or every
            # credential becomes permanently unusable after the next
            # save+reload cycle. The Fernet encryption below is what
            # keeps this safe at rest, not redaction.
            data["secret"] = cred.secret.get_secret_value()
            # Convert datetime to ISO format string for JSON serialization
            if isinstance(data.get("stored_at"), datetime):
                data["stored_at"] = data["stored_at"].isoformat()
            if isinstance(data.get("last_rotated"), datetime):
                data["last_rotated"] = data["last_rotated"].isoformat()
            credentials_data.append(data)
        payload = json.dumps({"credentials": credentials_data}, indent=2)
        encrypted = self._fernet.encrypt(payload.encode())

        # Write atomically (temp file + rename) so a crash mid-write can't
        # leave a truncated/corrupt store, and so concurrent readers only
        # ever see a fully-old or fully-new file, never a torn one.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._storage_path.parent), prefix=".credentials-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(encrypted)
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp_name, self._storage_path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        logger.debug("Credentials saved successfully")

    def add_credential(
        self,
        provider_id: str,
        account_name: str,
        secret: str,
        metadata: Optional[dict] = None,
    ) -> Credential:
        """Add a new credential.

        Args:
            provider_id: Provider identifier
            account_name: Human-readable account name
            secret: The actual API key or token
            metadata: Additional metadata

        Returns:
            The created Credential object

        Raises:
            CredentialError: If credential cannot be added
        """
        cred_id = f"{provider_id}:{account_name}"

        with self._locked():
            self._load_credentials()  # resync with disk before mutating

            # Check for duplicates
            if cred_id in self._credentials:
                logger.warning(f"Credential already exists: {cred_id}")
                raise CredentialError(f"Credential {cred_id} already exists")

            credential = Credential(
                id=cred_id,
                provider_id=provider_id,
                account_name=account_name,
                secret=SecretStr(secret),
                metadata=metadata or {},
            )

            self._credentials[cred_id] = credential
            self._save_credentials()

        logger.info(f"Added credential for {provider_id}/{account_name}")
        return credential

    def get_credential(self, provider_id: str, account_name: Optional[str] = None) -> Optional[Credential]:
        """Retrieve a credential.

        Args:
            provider_id: Provider identifier
            account_name: Optional account name filter

        Returns:
            Credential if found, None otherwise
        """
        if account_name:
            cred_id = f"{provider_id}:{account_name}"
        else:
            # Return first credential for this provider
            cred_id = next(
                (c.id for c in self._credentials.values() if c.provider_id == provider_id),
                None,
            )

        return self._credentials.get(cred_id)

    def list_credentials(self, provider_id: Optional[str] = None) -> list[Credential]:
        """List all credentials, optionally filtered by provider.

        Returns:
            List of Credential objects (with secrets redacted)
        """
        if provider_id:
            return [
                cred for cred in self._credentials.values()
                if cred.provider_id == provider_id
            ]
        return list(self._credentials.values())

    def remove_credential(self, provider_id: str, account_name: str) -> bool:
        """Remove a credential.

        Args:
            provider_id: Provider identifier
            account_name: Account name

        Returns:
            True if removed, False if not found
        """
        cred_id = f"{provider_id}:{account_name}"
        with self._locked():
            self._load_credentials()  # resync with disk before mutating
            if cred_id in self._credentials:
                del self._credentials[cred_id]
                self._save_credentials()
                logger.info(f"Removed credential: {cred_id}")
                return True
            return False

    def rotate_credential(
        self,
        provider_id: str,
        account_name: str,
        new_secret: str,
    ) -> Credential:
        """Rotate an existing credential.

        Args:
            provider_id: Provider identifier
            account_name: Account name
            new_secret: New API key or token

        Returns:
            Updated Credential object
        """
        cred_id = f"{provider_id}:{account_name}"
        with self._locked():
            self._load_credentials()  # resync with disk before mutating
            if cred_id not in self._credentials:
                raise CredentialNotFoundError(f"Credential not found: {cred_id}")

            credential = self._credentials[cred_id]
            credential.secret = SecretStr(new_secret)
            credential.last_rotated = datetime.utcnow()
            self._credentials[cred_id] = credential
            self._save_credentials()

        logger.info(f"Rotated credential: {cred_id}")
        return credential

    def rotate_master_key(self) -> None:
        """Rotate the Fernet master key protecting the whole credential store.

        Unlike rotate_credential() (which changes one stored secret's
        value), this re-keys the encryption itself - e.g. after a
        suspected key compromise, or as routine hygiene. The previous key
        is kept as a `.master_key.previous` backup until the new key and
        the re-encrypted store are both confirmed written, and is
        restored automatically if anything fails partway through, so a
        crash or error here can't leave the store permanently
        undecryptable.
        """
        key_path = self._key_path
        backup_path = key_path.with_name(key_path.name + ".previous")

        with self._locked():
            self._load_credentials()  # resync with current on-disk state/key first

            old_key_bytes = key_path.read_bytes() if key_path.exists() else None
            new_key = Fernet.generate_key()

            if old_key_bytes is not None:
                self._write_key_atomic(backup_path, old_key_bytes)

            try:
                self._write_key_atomic(key_path, new_key)
                self._fernet = Fernet(new_key)
                self._save_credentials()  # re-encrypts self._credentials under the new key
            except BaseException:
                # Roll back to the previous key so the store stays decryptable.
                if old_key_bytes is not None:
                    self._write_key_atomic(key_path, old_key_bytes)
                    self._fernet = Fernet(old_key_bytes)
                logger.error(
                    "Master key rotation failed and was rolled back; "
                    f"previous key also preserved at {backup_path}"
                )
                raise
            else:
                if backup_path.exists():
                    backup_path.unlink()

        logger.info("Rotated credential store master encryption key")

    def validate_provider_credentials(
        self,
        provider: ProviderDescriptor,
    ) -> tuple[bool, Optional[str]]:
        """Validate that required credentials exist for a provider.

        Args:
            provider: Provider descriptor

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not provider.requires_authentication:
            return True, None

        credentials = self.list_credentials(provider_id=provider.id)
        if not credentials:
            return False, f"No credentials configured for provider: {provider.id}"

        return True, None

    @property
    def credential_count(self) -> int:
        """Total number of stored credentials."""
        return len(self._credentials)

    def get_secret_value(self, provider_id: str, account_name: Optional[str] = None) -> Optional[str]:
        """Get the raw secret value for a credential.

        Args:
            provider_id: Provider identifier
            account_name: Optional account name filter

        Returns:
            The secret string value, or None if not found
        """
        cred = self.get_credential(provider_id, account_name)
        if cred:
            return cred.secret.get_secret_value()
        return None


# Type alias for convenience
CredentialManagerFactory = lambda: CredentialManager()
