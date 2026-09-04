"""Tests for routing engine."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from apiloop.routing.engine import (
    HealthAwareStrategy,
    QuotaAwareStrategy,
    RoundRobinStrategy,
    RoutingDecision,
    RoutingEngine,
    RouteResult,
    FailoverStrategy,
)
from apiloop.models import (
    ModelDescriptor,
    NormalizedRequest,
    ProviderDescriptor,
    ProviderHealth,
    ProviderKind,
    ChatMessage,
)


@pytest.fixture
def sample_providers():
    """Create sample providers for testing."""
    return [
        ProviderDescriptor(
            id="provider-a",
            name="Provider A",
            kind=ProviderKind.REMOTE,
            base_url="https://api.a.com/v1",
            health=ProviderHealth.HEALTHY,
        ),
        ProviderDescriptor(
            id="provider-b",
            name="Provider B",
            kind=ProviderKind.REMOTE,
            base_url="https://api.b.com/v1",
            health=ProviderHealth.DEGRADED,
        ),
        ProviderDescriptor(
            id="provider-c",
            name="Provider C",
            kind=ProviderKind.REMOTE,
            base_url="https://api.c.com/v1",
            health=ProviderHealth.UNAVAILABLE,
        ),
    ]


@pytest.fixture
def sample_models():
    """Create sample models for testing."""
    return [
        ModelDescriptor(
            id="model-1",
            provider_id="provider-a",
            display_name="Model 1",
        ),
        ModelDescriptor(
            id="model-1",
            provider_id="provider-b",
            display_name="Model 1 (B)",
        ),
        ModelDescriptor(
            id="model-2",
            provider_id="provider-a",
            display_name="Model 2",
        ),
    ]


@pytest.fixture
def sample_request():
    """Create a sample request."""
    return NormalizedRequest(
        model="model-1",
        messages=[ChatMessage(role="user", content="Hello")],
    )


class TestRoundRobinStrategy:
    @pytest.mark.asyncio
    async def test_round_robin_selection(self, sample_providers, sample_models, sample_request):
        """Test round-robin selects providers in order."""
        strategy = RoundRobinStrategy()
        result = await strategy.route(sample_request, sample_providers, sample_models)
        assert result.success is True
        assert result.provider_id in ["provider-a", "provider-b"]

    @pytest.mark.asyncio
    async def test_round_robin_skips_unavailable(self, sample_providers, sample_models, sample_request):
        """Test round-robin skips unavailable providers."""
        strategy = RoundRobinStrategy()
        result = await strategy.route(sample_request, sample_providers, sample_models)
        assert result.provider_id != "provider-c"

    @pytest.mark.asyncio
    async def test_round_robin_no_healthy_providers(self):
        """Test round-robin fails when no healthy providers."""
        providers = [
            ProviderDescriptor(
                id="bad",
                name="Bad",
                kind=ProviderKind.REMOTE,
                base_url="https://bad.com",
                health=ProviderHealth.UNAVAILABLE,
            ),
        ]
        strategy = RoundRobinStrategy()
        result = await strategy.route(sample_request, providers, [])
        assert result.success is False


class TestFailoverStrategy:
    @pytest.mark.asyncio
    async def test_failover_uses_priority_order(self):
        """Test failover respects priority order."""
        providers = [
            ProviderDescriptor(id="first", name="First", kind=ProviderKind.REMOTE, base_url="https://first.com", health=ProviderHealth.HEALTHY),
            ProviderDescriptor(id="second", name="Second", kind=ProviderKind.REMOTE, base_url="https://second.com", health=ProviderHealth.HEALTHY),
        ]
        models = [
            ModelDescriptor(id="m1", provider_id="first", display_name="M1"),
            ModelDescriptor(id="m1", provider_id="second", display_name="M1-S"),
        ]
        request = NormalizedRequest(model="m1", messages=[ChatMessage(role="user", content="Hi")])

        strategy = FailoverStrategy(priority_order=["first", "second"])
        result = await strategy.route(request, providers, models)
        assert result.success is True
        assert result.provider_id == "first"

    @pytest.mark.asyncio
    async def test_failover_falls_back(self):
        """Test failover falls back to second provider."""
        providers = [
            ProviderDescriptor(id="unavailable", name="Unavailable", kind=ProviderKind.REMOTE, base_url="https://fail.com", health=ProviderHealth.UNAVAILABLE),
            ProviderDescriptor(id="backup", name="Backup", kind=ProviderKind.REMOTE, base_url="https://backup.com", health=ProviderHealth.HEALTHY),
        ]
        models = [
            ModelDescriptor(id="m1", provider_id="backup", display_name="M1"),
        ]
        request = NormalizedRequest(model="m1", messages=[ChatMessage(role="user", content="Hi")])

        strategy = FailoverStrategy(priority_order=["unavailable", "backup"])
        result = await strategy.route(request, providers, models)
        assert result.success is True
        assert result.provider_id == "backup"


class TestHealthAwareStrategy:
    @pytest.mark.asyncio
    async def test_health_aware_selects_healthy(self, sample_providers, sample_models, sample_request):
        """Test health-aware selects the healthiest provider."""
        strategy = HealthAwareStrategy()
        result = await strategy.route(sample_request, sample_providers, sample_models)
        assert result.success is True
        # Should prefer HEALTHY over DEGRADED
        assert result.provider_id == "provider-a"

    @pytest.mark.asyncio
    async def test_health_aware_skips_unavailable(self, sample_providers, sample_models, sample_request):
        """Test health-aware skips unavailable providers."""
        strategy = HealthAwareStrategy()
        result = await strategy.route(sample_request, sample_providers, sample_models)
        assert result.provider_id != "provider-c"


class TestQuotaAwareStrategy:
    @pytest.mark.asyncio
    async def test_quota_aware_prioritizes_quota(self):
        """Test quota-aware prefers higher quota."""
        providers = [
            ProviderDescriptor(id="low", name="Low", kind=ProviderKind.REMOTE, base_url="https://low.com", health=ProviderHealth.HEALTHY),
            ProviderDescriptor(id="high", name="High", kind=ProviderKind.REMOTE, base_url="https://high.com", health=ProviderHealth.HEALTHY),
        ]
        models = [
            ModelDescriptor(id="m1", provider_id="low", display_name="M1", quota_remaining=100),
            ModelDescriptor(id="m1", provider_id="high", display_name="M1-H", quota_remaining=1000),
        ]
        request = NormalizedRequest(model="m1", messages=[ChatMessage(role="user", content="Hi")])

        strategy = QuotaAwareStrategy()
        result = await strategy.route(request, providers, models)
        assert result.success is True
        assert result.provider_id == "high"


class TestRoutingEngine:
    @pytest.mark.asyncio
    async def test_engine_initialization(self):
        """Test engine initialization."""
        engine = RoutingEngine(strategy="health_aware")
        assert engine._strategy_name == "health_aware"

    def test_add_and_remove_provider(self):
        """Test adding and removing providers."""
        engine = RoutingEngine()
        provider = ProviderDescriptor(
            id="test",
            name="Test",
            kind=ProviderKind.REMOTE,
            base_url="https://test.com",
        )
        engine.add_provider(provider)
        assert len(engine.providers) == 1
        assert engine.providers[0].id == "test"

        removed = engine.remove_provider("test")
        assert removed is True
        assert len(engine.providers) == 0

    def test_add_models(self):
        """Test adding models."""
        engine = RoutingEngine()
        model = ModelDescriptor(
            id="m1",
            provider_id="p1",
            display_name="Model 1",
        )
        engine.add_models([model])
        assert len(engine.models) == 1
        assert engine.models[0].id == "m1"

    @pytest.mark.asyncio
    async def test_routing_with_no_providers(self):
        """Test routing fails with no providers."""
        engine = RoutingEngine()
        request = NormalizedRequest(model="m1", messages=[ChatMessage(role="user", content="Hi")])
        result = await engine.route(request)
        assert result.success is False

    def test_strategy_switching(self):
        """Test switching routing strategies."""
        engine = RoutingEngine(strategy="round_robin")
        engine.update_strategy("failover", {"priority_order": ["a", "b"]})
        assert engine._strategy_name == "failover"

    def test_routing_history(self):
        """Test routing history tracking."""
        engine = RoutingEngine()
        # History should start empty
        assert len(engine.get_routing_history()) == 0
