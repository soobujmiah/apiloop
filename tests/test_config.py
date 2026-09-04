"""Configuration tests for APIloop."""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from apiloop.config import Config, load_config, validate_config
from apiloop.models import ProviderDescriptor, ProviderKind, ModelDescriptor


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory."""
    return tmp_path / "apiloop"


@pytest.fixture
def config_file(temp_config_dir):
    """Create a temporary config file."""
    return temp_config_dir / "config.yaml"


class TestConfig:
    def test_default_config_location(self):
        """Test default config path."""
        config = Config()
        assert config.config_path.name == "config.yaml"
        assert ".config" in str(config.config_path)

    def test_load_empty_config(self, config_file):
        """Test loading an empty config file."""
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("")
        config = Config(config_path=config_file)
        assert len(config.providers) == 0
        assert len(config.models) == 0

    def test_load_providers(self, config_file):
        """Test loading providers from config."""
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_data = {
            "providers": {
                "openai": {
                    "id": "openai",
                    "name": "OpenAI",
                    "kind": "remote",
                    "base_url": "https://api.openai.com/v1",
                    "requires_authentication": True,
                }
            }
        }
        config_file.write_text(yaml.dump(config_data))
        config = Config(config_path=config_file)
        assert "openai" in config.providers
        assert config.providers["openai"].base_url == "https://api.openai.com/v1"

    def test_save_and_load(self, config_file):
        """Test saving and loading config."""
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config = Config(config_path=config_file)
        provider = ProviderDescriptor(
            id="test",
            name="Test",
            kind=ProviderKind.REMOTE,
            base_url="https://test.com",
        )
        config.providers["test"] = provider
        config.save()

        # Reload
        config2 = Config(config_path=config_file)
        assert "test" in config2.providers
        assert config2.providers["test"].base_url == "https://test.com"

    def test_validate_empty_config(self):
        """Test validation of empty config."""
        config = Config()
        errors = validate_config(config)
        assert len(errors) == 0

    def test_validate_missing_provider_id(self):
        """Test validation catches missing provider ID."""
        config = Config()
        provider = ProviderDescriptor(
            id="",  # Missing ID
            name="Bad Provider",
            kind=ProviderKind.REMOTE,
            base_url="https://bad.com",
        )
        config.providers["bad"] = provider
        errors = validate_config(config)
        assert any("missing 'id'" in e for e in errors)

    def test_validate_missing_base_url(self):
        """Test validation catches missing base URL."""
        config = Config()
        provider = ProviderDescriptor(
            id="no-url",
            name="No URL",
            kind=ProviderKind.REMOTE,
            base_url="",  # Missing URL
        )
        config.providers["no-url"] = provider
        errors = validate_config(config)
        assert any("missing 'base_url'" in e for e in errors)

    def test_validate_invalid_model_provider(self):
        """Test validation catches invalid model provider reference."""
        config = Config()
        model = ModelDescriptor(
            id="model-1",
            provider_id="nonexistent",
            display_name="Model 1",
        )
        config.models["model-1"] = model
        errors = validate_config(config)
        assert any("references unknown provider" in e for e in errors)

    def test_get_provider(self):
        """Test getting a provider by ID."""
        config = Config()
        provider = ProviderDescriptor(
            id="test",
            name="Test",
            kind=ProviderKind.REMOTE,
            base_url="https://test.com",
        )
        config.providers["test"] = provider
        result = config.get_provider("test")
        assert result is not None
        assert result.id == "test"

    def test_get_provider_not_found(self):
        """Test getting non-existent provider."""
        config = Config()
        result = config.get_provider("nonexistent")
        assert result is None

    def test_add_provider(self):
        """Test adding a provider."""
        config = Config()
        provider = ProviderDescriptor(
            id="new",
            name="New",
            kind=ProviderKind.REMOTE,
            base_url="https://new.com",
        )
        config.add_provider(provider)
        assert "new" in config.providers

    def test_remove_provider(self):
        """Test removing a provider."""
        config = Config()
        provider = ProviderDescriptor(
            id="remove-me",
            name="Remove",
            kind=ProviderKind.REMOTE,
            base_url="https://remove.com",
        )
        config.providers["remove-me"] = provider
        result = config.remove_provider("remove-me")
        assert result is True
        assert "remove-me" not in config.providers

    def test_remove_nonexistent_provider(self):
        """Test removing non-existent provider."""
        config = Config()
        result = config.remove_provider("nonexistent")
        assert result is False


class TestConfigFilePermissions:
    def test_config_file_restrictive_permissions(self, config_file):
        """Test that saved config has restrictive permissions."""
        config = Config(config_path=config_file)
        provider = ProviderDescriptor(
            id="test",
            name="Test",
            kind=ProviderKind.REMOTE,
            base_url="https://test.com",
        )
        config.providers["test"] = provider
        config.save()
        # Check permissions are restrictive (owner only)
        stat_info = config_file.stat()
        assert stat_info.st_mode & 0o777 == 0o600


class TestDefaultConfig:
    def test_create_default_config(self, temp_config_dir):
        """Test creating default config."""
        from apiloop.config import create_default_config
        config = create_default_config()
        assert "example-openai" in config.providers
