"""Configuration management for APIloop."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from .models import ModelDescriptor, ProviderDescriptor, ProviderKind

logger = logging.getLogger(__name__)

def _default_config_path() -> Path:
    """Compute the default config path fresh on every call.

    Deliberately not a module-level constant: Path.home() must be
    re-evaluated per call, not frozen at import time, otherwise a process
    (or test) that changes $HOME after this module first loads - which is
    exactly what test isolation via monkeypatch.setenv("HOME", ...) does -
    silently keeps targeting whatever $HOME was active at import time
    instead. That previously caused tests with no explicit config_path to
    read and write the real ~/.config/apiloop/config.yaml.
    """
    return Path.home() / ".config" / "apiloop" / "config.yaml"


class Config:
    """APIloop configuration manager."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or _default_config_path()
        self.providers: dict[str, ProviderDescriptor] = {}
        self.models: dict[str, ModelDescriptor] = {}
        self.routing: dict[str, Any] = {
            "strategy": "health_aware",
            "failover_order": [],
        }
        self.logging: dict[str, Any] = {
            "level": "INFO",
            "redact_secrets": True,
        }
        self.api: dict[str, Any] = {
            "host": "127.0.0.1",
            "port": 8080,
            "cors_origins": ["http://localhost"],
        }
        # Non-fatal problems found while loading config.yaml: a malformed
        # entry is skipped rather than silently dropping everything after
        # it (or everything, via one aggregate warning) - this records
        # exactly what was skipped and why, for `apiloop doctor` and callers.
        self.load_errors: list[str] = []
        self._load()

    def _load(self) -> None:
        """Load configuration from file."""
        if not self.config_path.exists():
            logger.debug(f"Config file not found: {self.config_path}")
            return

        try:
            raw = self.config_path.read_text()
        except OSError as e:
            msg = f"Failed to read config file {self.config_path}: {e}"
            logger.warning(msg)
            self.load_errors.append(msg)
            return

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            msg = f"Config file {self.config_path} is not valid YAML: {e}"
            logger.error(msg)
            self.load_errors.append(msg)
            return

        if not data:
            return

        self._load_section(data, "providers", self.providers, ProviderDescriptor)
        self._load_section(data, "models", self.models, ModelDescriptor)

        # Load routing config
        if "routing" in data:
            self.routing.update(data["routing"])

        # Load logging config
        if "logging" in data:
            self.logging.update(data["logging"])

        # Load API config
        if "api" in data:
            self.api.update(data["api"])

        logger.debug(f"Loaded config from {self.config_path}")

    def _load_section(self, data: dict, key: str, target: dict, model_cls: type) -> None:
        """Load one id -> descriptor section (providers/models), skipping
        and recording individually malformed entries instead of letting one
        bad entry silently take down every entry after it in the file.
        """
        section = data.get(key, {}) or {}
        if not isinstance(section, dict):
            msg = f"'{key}' section is not a mapping (got {type(section).__name__}); ignoring"
            logger.warning(msg)
            self.load_errors.append(msg)
            return

        for entry_id, entry_data in section.items():
            try:
                target[entry_id] = model_cls(**entry_data)
            except Exception as e:
                msg = f"Skipped {key[:-1]} '{entry_id}': {e}"
                logger.warning(msg)
                self.load_errors.append(msg)

    def save(self) -> None:
        """Save configuration to file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "providers": {pid: p.model_dump() for pid, p in self.providers.items()},
            "models": {mid: m.model_dump() for mid, m in self.models.items()},
            "routing": self.routing,
            "logging": self.logging,
            "api": self.api,
        }

        # Remove sensitive fields before saving
        for pid, p in data["providers"].items():
            p.pop("credentials", None)

        self.config_path.write_text(yaml.dump(data, default_flow_style=False))
        # Restrict permissions
        self.config_path.chmod(0o600)
        logger.debug(f"Saved config to {self.config_path}")

    def get_provider(self, provider_id: str) -> Optional[ProviderDescriptor]:
        """Get a provider by ID."""
        return self.providers.get(provider_id)

    def add_provider(self, provider: ProviderDescriptor) -> None:
        """Add or update a provider."""
        self.providers[provider.id] = provider
        self.save()

    def remove_provider(self, provider_id: str) -> bool:
        """Remove a provider."""
        if provider_id in self.providers:
            del self.providers[provider_id]
            self.save()
            return True
        return False


# Module-level singleton
_config_instance: Optional[Config] = None


def get_config(config_path: Optional[Path] = None) -> Config:
    """Get or create the global config singleton."""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config(config_path)
    return _config_instance


def load_config(config_path: Optional[Path] = None) -> Config:
    """Load configuration (convenience function)."""
    return get_config(config_path)


def save_config(config: Config) -> None:
    """Save configuration."""
    config.save()


def validate_config(config: Config) -> list[str]:
    """Validate configuration and return list of errors."""
    errors = []

    # Check providers have required fields
    for pid, provider in config.providers.items():
        if not provider.id:
            errors.append(f"Provider '{pid}': missing 'id'")
        if not provider.base_url:
            errors.append(f"Provider '{pid}': missing 'base_url'")
        if not provider.name:
            errors.append(f"Provider '{pid}': missing 'name'")

    # Check models reference valid providers
    for mid, model in config.models.items():
        if model.provider_id not in config.providers:
            errors.append(f"Model '{mid}': references unknown provider '{model.provider_id}'")

    return errors


def create_default_config() -> Config:
    """Create a default configuration file."""
    config = Config()

    # Add default providers section
    config.providers["example-openai"] = ProviderDescriptor(
        id="example-openai",
        name="Example OpenAI",
        kind=ProviderKind.REMOTE,
        base_url="https://api.openai.com/v1",
        requires_authentication=True,
        authentication_method="bearer_token",
        supports_streaming=True,
        notes="Example provider - replace with your actual configuration",
    )

    config.save()
    logger.info(f"Created default config at {config.config_path}")
    return config
