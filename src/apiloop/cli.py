"""CLI for APIloop."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click

from apiloop.credentials.manager import CredentialManager
from apiloop.providers.base import ProviderAdapterFactory
from apiloop.models import ProviderDescriptor, ProviderKind, ModelDescriptor, ModelAvailability

logger = logging.getLogger(__name__)


class AliasedGroup(click.Group):
    """Click group that supports command aliases."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._aliases = {}

    def add_alias(self, original: str, alias: str) -> None:
        """Add an alias for a command."""
        self._aliases[alias] = original

    def get_command(self, ctx, cmd_name):
        """Resolve aliases before looking up commands."""
        if cmd_name in self._aliases:
            cmd_name = self._aliases[cmd_name]
        return super().get_command(ctx, cmd_name)


@click.group(cls=AliasedGroup)
@click.version_option(version="0.1.0")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--config", "-c", type=click.Path(), help="Path to config file")
@click.pass_context
def cli(ctx, verbose, config):
    """APIloop - Provider-agnostic AI API gateway and router.

    APIloop lets you configure multiple AI providers once and interact with them
    through a unified interface. It handles routing, failover, credential
    management, and capability discovery across providers.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["config_path"] = config or Path.home() / ".config" / "apiloop" / "config.yaml"

    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)


# ─── Provider Commands ───────────────────────────────────────────────────────

@cli.group()
def provider():
    """Manage AI providers."""
    pass


@provider.command()
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text", help="Output format")
def list(fmt):
    """List configured providers."""
    from apiloop.config import load_config
    config = load_config()

    if not config.providers:
        click.echo("No providers configured.")
        click.echo("")
        click.echo("Add a provider with:")
        click.echo('  apiloop provider add --name openai --type openai_compatible')
        return

    if fmt == "json":
        click.echo(json.dumps(config.providers, indent=2, default=str))
    else:
        click.echo(f"{'ID':<25} {'Name':<30} {'Kind':<10} {'Health':<15}")
        click.echo("-" * 80)
        for p in config.providers.values():
            click.echo(f"{p.id:<25} {p.name:<30} {p.kind.value:<10} {p.health.value:<15}")


@provider.command()
@click.argument("name")
@click.option("--type", "provider_type", required=True, help="Provider type (e.g., openai_compatible)")
@click.option("--base-url", required=True, help="Base URL of the API endpoint")
@click.option("--kind", type=click.Choice(["local", "remote", "hybrid"]), default="remote", help="Provider kind")
@click.option("--auth-method", type=click.Choice(["bearer_token", "api_key", "none"]), default="bearer_token", help="Authentication method")
@click.option("--streaming/--no-streaming", default=True, help="Support streaming")
def add(name, provider_type, base_url, kind, auth_method, streaming):
    """Add a new provider configuration.

    NAME: Unique identifier for this provider (e.g., 'openai', 'ollama-local')

    Example:
        apiloop provider add my-openai --type openai_compatible --base-url https://api.openai.com/v1
    """
    from apiloop.config import save_config, load_config
    from apiloop.models import ProviderDescriptor, ProviderKind

    config = load_config()

    # Check for duplicates
    if name in config.providers:
        click.echo(f"Error: Provider '{name}' already exists.", err=True)
        click.echo("Use 'apiloop provider update' to modify existing providers.", err=True)
        sys.exit(1)

    # Map string to enum
    kind_map = {"local": ProviderKind.LOCAL, "remote": ProviderKind.REMOTE, "hybrid": ProviderKind.HYBRID}
    provider_kind = kind_map[kind]

    provider = ProviderDescriptor(
        id=name,
        name=name,
        kind=provider_kind,
        adapter_type=provider_type,
        base_url=base_url.rstrip("/"),
        authentication_method=auth_method,
        supports_streaming=streaming,
    )

    config.providers[name] = provider
    save_config(config)

    click.echo(f"✓ Provider '{name}' added successfully.")
    click.echo(f"  Base URL: {base_url}")
    click.echo(f"  Kind: {provider_kind.value}")
    click.echo("")
    click.echo("Next steps:")
    click.echo(f"  apiloop provider credential add {name} --account default")


@provider.command()
@click.argument("name")
@click.option("--account", default="default", help="Account name")
@click.option("--api-key", prompt="API Key", hide_input=True, confirmation_prompt=False, help="API key")
def credential_add(name, account, api_key):
    """Add credentials for a provider.

    NAME: Provider identifier
    ACCOUNT: Account name (for multiple credentials per provider)
    """
    from apiloop.config import load_config
    from apiloop.credentials.manager import CredentialManager

    config = load_config()
    if name not in config.providers:
        click.echo(f"Error: Provider '{name}' not found.", err=True)
        sys.exit(1)

    cm = CredentialManager()
    try:
        cm.add_credential(
            provider_id=name,
            account_name=account,
            secret=api_key,
        )
        click.echo(f"✓ Credential added for {name}/{account}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@provider.command()
@click.argument("name")
def test(name):
    """Test connectivity to a provider.

    NAME: Provider identifier
    """
    from apiloop.config import load_config
    from apiloop.providers.base import ProviderAdapterFactory
    from apiloop.credentials.manager import CredentialManager

    config = load_config()
    if name not in config.providers:
        click.echo(f"Error: Provider '{name}' not found.", err=True)
        sys.exit(1)

    provider = config.providers[name]
    cm = CredentialManager()
    adapter_factory = ProviderAdapterFactory()
    adapter = adapter_factory.create(provider, cm)

    click.echo(f"Testing provider: {name} ({provider.base_url})")
    click.echo("")

    try:
        # Health check
        healthy, msg = asyncio.run(adapter.health_check())
        if healthy:
            click.echo(f"✓ Health: {msg}")
        else:
            click.echo(f"⚠ Health: {msg}")

        # Try to list models
        models = asyncio.run(adapter.list_models())
        if models:
            click.echo(f"✓ Models discovered: {len(models)}")
            for m in models[:5]:
                click.echo(f"  - {m.id}")
            if len(models) > 5:
                click.echo(f"  ... and {len(models) - 5} more")
        else:
            click.echo("  No models discovered (this may be normal for some providers)")

    except Exception as e:
        click.echo(f"✗ Test failed: {e}", err=True)
        sys.exit(1)


# ─── Model Commands ──────────────────────────────────────────────────────────

@cli.group()
def model():
    """Manage model registry."""
    pass


@model.command()
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text", help="Output format")
def list(fmt):
    """List registered models."""
    from apiloop.config import load_config
    config = load_config()

    if not config.models:
        click.echo("No models registered.")
        click.echo("")
        click.echo("Models are auto-discovered when providers are tested.")
        return

    if fmt == "json":
        click.echo(json.dumps([m.model_dump() for m in config.models.values()], indent=2, default=str))
    else:
        click.echo(f"{'Model ID':<40} {'Provider':<20} {'Status':<12}")
        click.echo("-" * 72)
        for mid, model in config.models.items():
            click.echo(f"{model.id:<40} {model.provider_id:<20} {model.availability.value:<12}")


@model.command()
@click.argument("provider")
def refresh(provider):
    """Refresh model list from a provider.

    PROVIDER: Provider identifier
    """
    from apiloop.config import load_config
    from apiloop.providers.base import ProviderAdapterFactory
    from apiloop.credentials.manager import CredentialManager

    config = load_config()
    if provider not in config.providers:
        click.echo(f"Error: Provider '{provider}' not found.", err=True)
        sys.exit(1)

    provider_desc = config.providers[provider]
    cm = CredentialManager()
    adapter = ProviderAdapterFactory.create(provider_desc, cm)

    click.echo(f"Refreshing models for: {provider}")
    models = asyncio.run(adapter.list_models())

    if models:
        config.models = {}
        for m in models:
            config.models[m.id] = m
            m.provider_id = provider
        from apiloop.config import save_config
        save_config(config)
        click.echo(f"✓ Discovered {len(models)} models")
    else:
        click.echo("  No models discovered (provider may not expose model list)")


# ─── Route Commands ──────────────────────────────────────────────────────────

@cli.group()
def route():
    """Routing configuration and testing."""
    pass


@route.command()
@click.option("--strategy", default="health_aware", help="Routing strategy")
def test(strategy):
    """Test routing configuration.

    STRATEGY: Routing strategy (round_robin, failover, health_aware, quota_aware)
    """
    from apiloop.config import load_config
    from apiloop.routing.engine import RoutingEngine
    from apiloop.models import NormalizedRequest, ChatMessage

    config = load_config()

    if not config.providers:
        click.echo("No providers configured. Add providers first.")
        return

    engine = RoutingEngine(strategy=strategy)
    for provider in config.providers.values():
        engine.add_provider(provider)

    # Create a test request
    request = NormalizedRequest(
        model="test-model",
        messages=[ChatMessage(role="user", content="Hello")],
    )

    result = asyncio.run(engine.route(request))
    if result.success:
        click.echo(f"✓ Routing successful using '{strategy}' strategy")
        click.echo(f"  Selected: {result.provider_id}/{result.model_id}")
    else:
        click.echo(f"✗ Routing failed: {result.error}")


# ─── System Commands ─────────────────────────────────────────────────────────

@cli.command()
@click.option("--host", default=None, help="Host to bind (overrides config)")
@click.option("--port", default=None, type=int, help="Port to bind (overrides config)")
def start(host, port):
    """Start the APIloop gateway server."""
    import uvicorn
    from apiloop.config import load_config

    config = load_config()
    bind_host = host or config.api.get("host", "127.0.0.1")
    bind_port = port or config.api.get("port", 8080)

    click.echo(f"Starting APIloop gateway on {bind_host}:{bind_port}")
    uvicorn.run("apiloop.api.app:app", host=bind_host, port=bind_port)


@cli.command()
def doctor():
    """Run diagnostics and report system status."""
    from apiloop.config import load_config, validate_config
    from apiloop.credentials.manager import CredentialManager

    click.echo("APIloop Diagnostic Report")
    click.echo("=" * 60)
    click.echo("")

    # Config validation
    config = load_config()
    errors = validate_config(config)

    if errors:
        click.echo("❌ Configuration Errors:")
        for error in errors:
            click.echo(f"  - {error}")
        click.echo("")
    else:
        click.echo("✓ Configuration valid")
        click.echo("")

    # Provider status
    click.echo("Providers:")
    if config.providers:
        for pid, p in config.providers.items():
            cm = CredentialManager()
            cred = cm.get_credential(pid)
            has_cred = "✓" if cred else "✗"
            click.echo(f"  [{has_cred}] {pid}: {p.base_url} ({p.kind.value})")
    else:
        click.echo("  No providers configured")
    click.echo("")

    # Model count
    click.echo(f"Registered models: {len(config.models)}")
    click.echo("")

    # Credential storage
    cm = CredentialManager()
    click.echo(f"Stored credentials: {cm.credential_count}")
    click.echo("")

    # Python environment
    click.echo("Environment:")
    click.echo(f"  Python: {sys.version.split()[0]}")
    click.echo(f"  Platform: {sys.platform}")
    import platform
    click.echo(f"  Machine: {platform.machine()}")
    click.echo("")

    click.echo("✓ Diagnostic complete")


@cli.command()
def version():
    """Show version information."""
    import apiloop
    click.echo(f"APIloop version {apiloop.__version__}")
    click.echo(f"Python {sys.version.split()[0]}")


# ─── Main Entry Point ───────────────────────────────────────────────────────

def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
