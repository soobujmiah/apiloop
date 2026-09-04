"""Tests for the APIloop CLI."""

import json

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

    def test_list_json_output_is_parseable(self, runner):
        """Regression: --format json used to fall through Pydantic objects
        to json.dumps(default=str), producing repr strings as values
        instead of nested JSON - unusable for any scripting/automation."""
        runner.invoke(cli, [
            "provider", "add", "my-openai",
            "--type", "openai_compatible", "--base-url", "https://api.openai.com/v1",
        ])
        result = runner.invoke(cli, ["provider", "list", "--format", "json"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert isinstance(data["my-openai"], dict)
        assert data["my-openai"]["base_url"] == "https://api.openai.com/v1"
        assert data["my-openai"]["adapter_type"] == "openai_compatible"


class TestProviderCredential:
    """Regression coverage: the README documents `apiloop provider
    credential add NAME --api-key ...`, but that command didn't exist -
    the actual registered command was the flat `credential-add`."""

    def test_credential_add_nested_command_matches_docs(self, runner):
        runner.invoke(cli, [
            "provider", "add", "my-openai",
            "--type", "openai_compatible", "--base-url", "https://api.openai.com/v1",
        ])
        result = runner.invoke(cli, [
            "provider", "credential", "add", "my-openai",
            "--account", "default", "--api-key", "sk-test-key-12345",
        ])
        assert result.exit_code == 0
        assert "✓" in result.output

    def test_credential_add_requires_existing_provider(self, runner):
        result = runner.invoke(cli, [
            "provider", "credential", "add", "nonexistent",
            "--account", "default", "--api-key", "sk-test-key-12345",
        ])
        assert result.exit_code == 1


class TestDoctor:
    def test_doctor_handles_corrupted_credential_store_without_crashing(self, runner, tmp_path):
        """Regression: a corrupted credential store used to crash `doctor`
        itself (first as an unhandled NameError - see M1 - then, after that
        was fixed, as an unhandled CredentialEncryptionError) instead of
        being reported cleanly by the one command whose whole job is
        diagnosing exactly this kind of problem."""
        cred_path = tmp_path / ".config" / "apiloop" / "credentials.json"
        cred_path.parent.mkdir(parents=True, exist_ok=True)
        cred_path.write_text("not-valid-fernet-ciphertext-" + "x" * 60)

        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "Credential store error" in result.output
        assert "Diagnostic complete" in result.output


class TestRotateMasterKey:
    def test_rotate_master_key_preserves_credentials(self, runner):
        runner.invoke(cli, [
            "provider", "add", "my-openai",
            "--type", "openai_compatible", "--base-url", "https://api.openai.com/v1",
        ])
        runner.invoke(cli, [
            "provider", "credential", "add", "my-openai",
            "--account", "default", "--api-key", "sk-rotate-me-12345",
        ])

        result = runner.invoke(cli, ["rotate-master-key", "--yes"])
        assert result.exit_code == 0

        from apiloop.credentials.manager import CredentialManager
        cm = CredentialManager()
        assert cm.get_secret_value("my-openai", "default") == "sk-rotate-me-12345"
