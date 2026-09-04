"""Tests for the APIloop CLI."""

import pytest
from click.testing import CliRunner

import apiloop.config as config_module
from apiloop.cli import cli


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Isolate config state per test and reset the Config module-level
    singleton, which the CLI relies on via load_config()."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_module._config_instance = None
    yield
    config_module._config_instance = None


@pytest.fixture
def runner():
    return CliRunner()


def _reload_provider(name):
    config_module._config_instance = None
    from apiloop.config import load_config
    return load_config().get_provider(name)


class TestProviderAdd:
    """Regression coverage: --type was previously parsed and discarded
    (adapter_type never landed on the descriptor, so every provider
    silently fell through to the OpenAI-compatible adapter), and
    requires_authentication could never be set at all via the CLI."""

    def test_type_flag_sets_adapter_type(self, runner):
        result = runner.invoke(cli, [
            "provider", "add", "my-claude",
            "--type", "anthropic", "--base-url", "https://api.anthropic.com",
        ])
        assert result.exit_code == 0
        provider = _reload_provider("my-claude")
        assert provider.adapter_type == "anthropic"

    def test_requires_auth_defaults_true_for_real_auth_method(self, runner):
        result = runner.invoke(cli, [
            "provider", "add", "my-openai",
            "--type", "openai_compatible", "--base-url", "https://api.openai.com/v1",
        ])
        assert result.exit_code == 0
        provider = _reload_provider("my-openai")
        assert provider.requires_authentication is True

    def test_requires_auth_defaults_false_for_auth_method_none(self, runner):
        result = runner.invoke(cli, [
            "provider", "add", "my-ollama",
            "--type", "openai_compatible", "--base-url", "http://localhost:11434",
            "--auth-method", "none",
        ])
        assert result.exit_code == 0
        provider = _reload_provider("my-ollama")
        assert provider.requires_authentication is False

    def test_requires_auth_explicit_override(self, runner):
        result = runner.invoke(cli, [
            "provider", "add", "my-provider",
            "--type", "openai_compatible", "--base-url", "https://x.com",
            "--no-requires-auth",
        ])
        assert result.exit_code == 0
        provider = _reload_provider("my-provider")
        assert provider.requires_authentication is False

    def test_requires_auth_explicit_override_wins_over_auth_method(self, runner):
        """--requires-auth should win even if --auth-method would default False."""
        result = runner.invoke(cli, [
            "provider", "add", "my-provider2",
            "--type", "openai_compatible", "--base-url", "https://x.com",
            "--auth-method", "none", "--requires-auth",
        ])
        assert result.exit_code == 0
        provider = _reload_provider("my-provider2")
        assert provider.requires_authentication is True
