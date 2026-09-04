"""Routing engine for APIloop.

Implements multiple routing strategies including:
- Round-robin
- Failover
- Health-aware
- Quota-aware (when available)
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..models import (
    ModelDescriptor,
    NormalizedRequest,
    NormalizedResponse,
    ProviderDescriptor,
    ProviderHealth,
)

logger = logging.getLogger(__name__)


class RoutingDecision(BaseModel):
    """Records the details of a routing decision."""

    strategy: str
    selected_provider: str
    rejected_providers: list[str] = []
    rejection_reasons: dict[str, str] = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteResult(BaseModel):
    """Result of a routing attempt."""

    success: bool
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    decision: Optional[RoutingDecision] = None
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class RoutingStrategy:
    """Base class for routing strategies."""

    def __init__(self, name: str, config: dict[str, Any] = None):
        self.name = name
        self.config = config or {}

    async def route(
        self,
        request: NormalizedRequest,
        providers: list[ProviderDescriptor],
        models: list[ModelDescriptor],
    ) -> RouteResult:
        """Determine which provider/model to use.

        Args:
            request: The incoming request
            providers: Available providers
            models: Available models

        Returns:
            RouteResult with decision details
        """
        raise NotImplementedError


class RoundRobinStrategy(RoutingStrategy):
    """Round-robin routing across healthy providers."""

    def __init__(self):
        super().__init__("round_robin")
        self._counters: dict[str, int] = defaultdict(int)

    async def route(
        self,
        request: NormalizedRequest,
        providers: list[ProviderDescriptor],
        models: list[ModelDescriptor],
    ) -> RouteResult:
        # Filter to healthy providers
        healthy = [p for p in providers if p.health in (ProviderHealth.HEALTHY, ProviderHealth.DEGRADED)]

        if not healthy:
            return RouteResult(
                success=False,
                error="No healthy providers available",
            )

        # Select next provider in round-robin fashion
        provider = healthy[self._counters[request.model] % len(healthy)]
        self._counters[request.model] += 1

        # Find matching model
        model = next((m for m in models if m.id == request.model and m.provider_id == provider.id), None)

        return RouteResult(
            success=True,
            provider_id=provider.id,
            model_id=model.id if model else request.model,
            decision=RoutingDecision(
                strategy=self.name,
                selected_provider=provider.id,
            ),
        )


class FailoverStrategy(RoutingStrategy):
    """Priority-based failover routing."""

    def __init__(self, priority_order: list[str] = None):
        super().__init__("failover")
        self._priority_order = priority_order or []

    async def route(
        self,
        request: NormalizedRequest,
        providers: list[ProviderDescriptor],
        models: list[ModelDescriptor],
    ) -> RouteResult:
        # Build priority-ordered provider list
        ordered_providers = []
        for pid in self._priority_order:
            if pid in [p.id for p in providers]:
                ordered_providers.append(next(p for p in providers if p.id == pid))

        # Add remaining providers
        for p in providers:
            if p not in ordered_providers:
                ordered_providers.append(p)

        rejected = []
        for provider in ordered_providers:
            if provider.health == ProviderHealth.UNAVAILABLE:
                rejected.append(provider.id)
                continue

            # Check if provider has the requested model
            model = next((m for m in models if m.id == request.model and m.provider_id == provider.id), None)
            if model:
                return RouteResult(
                    success=True,
                    provider_id=provider.id,
                    model_id=model.id,
                    decision=RoutingDecision(
                        strategy=self.name,
                        selected_provider=provider.id,
                        rejected_providers=rejected,
                    ),
                )

        return RouteResult(
            success=False,
            error="No provider has the requested model available",
            decision=RoutingDecision(
                strategy=self.name,
                rejected_providers=[p.id for p in providers],
            ),
        )


class HealthAwareStrategy(RoutingStrategy):
    """Route to the healthiest available provider."""

    def __init__(self):
        super().__init__("health_aware")

    async def route(
        self,
        request: NormalizedRequest,
        providers: list[ProviderDescriptor],
        models: list[ModelDescriptor],
    ) -> RouteResult:
        # Score providers by health
        health_scores = {
            ProviderHealth.HEALTHY: 3,
            ProviderHealth.DEGRADED: 2,
            ProviderHealth.UNKNOWN: 1,
            ProviderHealth.UNAVAILABLE: 0,
            ProviderHealth.AUTHENTICATION_FAILED: 0,
            ProviderHealth.RATE_LIMITED: 0,
        }

        scored_providers = []
        for provider in providers:
            score = health_scores.get(provider.health, 0)
            if score > 0:
                # Check model availability
                model = next((m for m in models if m.id == request.model and m.provider_id == provider.id), None)
                if model:
                    scored_providers.append((score, provider.id, model))

        if not scored_providers:
            return RouteResult(
                success=False,
                error="No provider with required model is available",
            )

        # Sort by score descending, then by provider ID for stability
        scored_providers.sort(key=lambda x: (-x[0], x[1]))
        best_score, best_provider_id, best_model = scored_providers[0]

        return RouteResult(
            success=True,
            provider_id=best_provider_id,
            model_id=best_model.id,
            decision=RoutingDecision(
                strategy=self.name,
                selected_provider=best_provider_id,
                metadata={"health_score": best_score},
            ),
        )


class QuotaAwareStrategy(RoutingStrategy):
    """Route considering quota and rate limits."""

    def __init__(self):
        super().__init__("quota_aware")

    async def route(
        self,
        request: NormalizedRequest,
        providers: list[ProviderDescriptor],
        models: list[ModelDescriptor],
    ) -> RouteResult:
        # Filter out rate-limited or unavailable providers
        available = [
            p for p in providers
            if p.health not in (ProviderHealth.UNAVAILABLE, ProviderHealth.RATE_LIMITED, ProviderHealth.AUTHENTICATION_FAILED)
        ]

        if not available:
            return RouteResult(
                success=False,
                error="All providers are unavailable or rate-limited",
            )

        # Sort by quota remaining (descending), then health
        def sort_key(provider):
            model = next((m for m in models if m.id == request.model and m.provider_id == provider.id), None)
            quota = model.quota_remaining if model else float('inf')
            health_score = {
                ProviderHealth.HEALTHY: 3,
                ProviderHealth.DEGRADED: 2,
                ProviderHealth.UNKNOWN: 1,
            }.get(provider.health, 0)
            return (-quota, -health_score)

        available.sort(key=sort_key)
        best_provider = available[0]

        # Find matching model
        model = next((m for m in models if m.id == request.model and m.provider_id == best_provider.id), None)

        return RouteResult(
            success=True,
            provider_id=best_provider.id,
            model_id=model.id if model else request.model,
            decision=RoutingDecision(
                strategy=self.name,
                selected_provider=best_provider.id,
            ),
        )


class RoutingEngine:
    """Main routing engine that coordinates provider selection."""

    STRATEGIES = {
        "round_robin": RoundRobinStrategy,
        "failover": FailoverStrategy,
        "health_aware": HealthAwareStrategy,
        "quota_aware": QuotaAwareStrategy,
    }

    def __init__(self, strategy: str = "health_aware", config: dict[str, Any] = None):
        """Initialize the routing engine.

        Args:
            strategy: Name of the routing strategy to use
            config: Strategy-specific configuration
        """
        self._strategy_name = strategy
        self._config = config or {}
        self._strategy = self._create_strategy(strategy, config)
        self._providers: list[ProviderDescriptor] = []
        self._models: list[ModelDescriptor] = []
        self._history: list[RoutingDecision] = []

    def _create_strategy(self, strategy_name: str, config: dict) -> RoutingStrategy:
        """Create a routing strategy instance."""
        strategy_class = self.STRATEGIES.get(strategy_name)
        if not strategy_class:
            logger.warning(f"Unknown strategy '{strategy_name}', falling back to health_aware")
            strategy_class = HealthAwareStrategy

        if strategy_name == "failover" and config:
            return strategy_class(config.get("priority_order", []))
        return strategy_class()

    def add_provider(self, provider: ProviderDescriptor) -> None:
        """Add a provider to the routing pool."""
        self._providers.append(provider)
        logger.info(f"Added provider: {provider.id} ({provider.name})")

    def remove_provider(self, provider_id: str) -> bool:
        """Remove a provider from the routing pool."""
        for i, p in enumerate(self._providers):
            if p.id == provider_id:
                self._providers.pop(i)
                logger.info(f"Removed provider: {provider_id}")
                return True
        return False

    def add_models(self, models: list[ModelDescriptor]) -> None:
        """Add models to the registry."""
        for model in models:
            # Update or add
            for i, existing in enumerate(self._models):
                if existing.id == model.id and existing.provider_id == model.provider_id:
                    self._models[i] = model
                    break
            else:
                self._models.append(model)

    async def route(self, request: NormalizedRequest) -> RouteResult:
        """Route a request to the best provider/model.

        Args:
            request: The normalized request

        Returns:
            RouteResult with provider and model selection
        """
        result = await self._strategy.route(request, self._providers, self._models)

        if result.success:
            self._history.append(result.decision)
            logger.debug(
                f"Routed to {result.provider_id}/{result.model_id} using {self._strategy_name}"
            )
        else:
            logger.warning(f"Routing failed: {result.error}")

        return result

    @property
    def providers(self) -> list[ProviderDescriptor]:
        """Get all registered providers."""
        return self._providers.copy()

    @property
    def models(self) -> list[ModelDescriptor]:
        """Get all registered models."""
        return self._models.copy()

    def get_routing_history(self, limit: int = 100) -> list[RoutingDecision]:
        """Get recent routing decisions."""
        return self._history[-limit:]

    def update_strategy(self, strategy_name: str, config: dict = None) -> None:
        """Change the routing strategy."""
        self._strategy_name = strategy_name
        self._config = config or {}
        self._strategy = self._create_strategy(strategy_name, self._config)
        logger.info(f"Switched routing strategy to: {strategy_name}")
