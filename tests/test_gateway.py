"""Tests for the FastAPI gateway: authentication boundary and debug endpoint gating."""

from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

import apiloop.config as config_module


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Isolate config/credentials/gateway-key state per test, and reset the
    Config module-level singleton that api/app.py relies on via load_config() -
    without this, state leaks between tests since get_config() caches globally.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    config_module._config_instance = None
    yield
    config_module._config_instance = None


@pytest.fixture
def client():
    from apiloop.api.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def configured_client(tmp_path):
    """A gateway with one provider + model already configured, so requests
    can reach the adapter-selection stage instead of failing at routing."""
    config_dir = tmp_path / ".config" / "apiloop"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(yaml.dump({
        "providers": {
            "test-provider": {
                "id": "test-provider", "name": "Test", "kind": "remote",
                "base_url": "https://example.com/v1", "requires_authentication": False,
            }
        },
        "models": {
            "test-model": {"id": "test-model", "provider_id": "test-provider", "display_name": "Test Model"},
        },
    }))

    from apiloop.api.app import app
    with TestClient(app) as c:
        yield c


class TestGatewayAuth:
    def test_chat_completions_requires_auth(self, client):
        r = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 401

    def test_chat_completions_rejects_wrong_key(self, client):
        r = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer not-the-real-key"},
        )
        assert r.status_code == 401

    def test_chat_completions_accepts_generated_key(self, client):
        key = client.app.state.gateway_api_key
        r = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        # No providers configured, so routing fails - but auth itself must
        # pass (503 "routing failed", not 401 "invalid key").
        assert r.status_code == 503

    def test_models_requires_auth(self, client):
        assert client.get("/v1/models").status_code == 401

    def test_debug_config_requires_auth(self, client):
        assert client.get("/debug/config").status_code == 401

    def test_debug_config_disabled_by_default_even_with_valid_key(self, client):
        key = client.app.state.gateway_api_key
        r = client.get("/debug/config", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 404

    def test_health_endpoint_is_public(self, client):
        assert client.get("/health").status_code == 200

    def test_v1_health_endpoint_is_public(self, client):
        assert client.get("/v1/health").status_code == 200

    def test_root_endpoint_is_public(self, client):
        assert client.get("/").status_code == 200


class TestGatewayKeyStorage:
    def test_key_persists_and_is_reused(self, tmp_path):
        from apiloop.api.app import _load_or_create_gateway_key

        key_path = tmp_path / ".gateway_key"
        key1 = _load_or_create_gateway_key(key_path)
        key2 = _load_or_create_gateway_key(key_path)
        assert key1 == key2

    def test_key_file_has_restrictive_permissions(self, tmp_path):
        from apiloop.api.app import _load_or_create_gateway_key

        key_path = tmp_path / ".gateway_key"
        _load_or_create_gateway_key(key_path)
        assert key_path.stat().st_mode & 0o777 == 0o600


class TestAdapterLifecycle:
    """Regression coverage: the gateway used to construct a fresh adapter
    (and therefore a fresh, never-closed httpx.AsyncClient) on every single
    request instead of reusing one per provider and closing it on shutdown."""

    def test_adapter_is_reused_across_requests(self, configured_client):
        import apiloop.api.app as app_module

        fake_adapter = AsyncMock()
        fake_adapter.chat_completion.side_effect = Exception("boom - no real network call expected")

        with patch.object(app_module.ProviderAdapterFactory, "create", return_value=fake_adapter) as mock_create:
            key = configured_client.app.state.gateway_api_key
            headers = {"Authorization": f"Bearer {key}"}
            body = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}

            configured_client.post("/v1/chat/completions", json=body, headers=headers)
            configured_client.post("/v1/chat/completions", json=body, headers=headers)

        assert mock_create.call_count == 1

    def test_adapters_are_closed_on_shutdown(self, tmp_path):
        config_module._config_instance = None
        from apiloop.api.app import app

        with TestClient(app) as c:
            fake_adapter = AsyncMock()
            c.app.state.adapters["fake-provider"] = fake_adapter
        # Exiting the TestClient context triggers the lifespan shutdown handler.
        fake_adapter.close.assert_awaited_once()
