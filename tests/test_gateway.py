"""Tests for the FastAPI gateway: authentication boundary and debug endpoint gating."""

import pytest
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
