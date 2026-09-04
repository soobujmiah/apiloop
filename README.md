# APIloop

Provider-agnostic AI API gateway, router, orchestrator, and credential manager.

## What is APIloop?

APIloop is a unified interface for interacting with multiple AI providers. Configure once, interact through a single endpoint.

## Features

- **Multi-provider support**: Connect to OpenAI, Anthropic, Google Gemini, Ollama, and custom endpoints
- **Intelligent routing**: Health-aware, quota-aware, and failover routing strategies
- **Secure credentials**: Encrypted storage with automatic redaction
- **Model registry**: Centralized model catalog with capability tracking
- **OpenAI-compatible API**: Drop-in replacement for existing OpenAI clients
- **CLI tools**: Easy configuration and management from the command line

## Installation

```bash
git clone https://github.com/soobujmiah/apiloop.git
cd apiloop
pip install -e ".[dev]"
apiloop init
```

## Quick Start

```bash
# Add a provider
apiloop provider add my-openai --type openai_compatible --base-url https://api.openai.com/v1
apiloop provider credential add my-openai --api-key sk-...

# Test connectivity
apiloop provider test my-openai

# Start the gateway
apiloop start

# Use with OpenAI client
export OPENAI_BASE_URL=http://127.0.0.1:8080/v1
python -c "from openai import OpenAI; client = OpenAI(); print(client.models.list())"
```

## Architecture

```
Client
   |
   v
APIloop Gateway (FastAPI)
   |
   +--> Capability Resolver
   |
   +--> Model Registry
   |
   +--> Provider Router
   |
   +--> Credential Manager
   |
   +--> Provider Adapter
   |
   v
Provider API (OpenAI, Anthropic, Ollama, etc.)
```

## Configuration

Configuration is stored in `~/.config/apiloop/config.yaml`. Secrets are stored separately in `~/.config/apiloop/credentials.json` (encrypted).

## Security

- API keys are encrypted at rest using Fernet symmetric encryption
- No secrets are logged or exposed in error messages
- Credentials are never committed to version control
- Provider adapters sanitize all outputs

**Threat model for the encrypted credential store:** the Fernet master key
lives at `~/.config/apiloop/.master_key`, next to the encrypted
`credentials.json`, both restricted to owner-only file permissions
(`0600`). This protects against accidental disclosure - committing the
directory to git, casual snooping, a backup tool that doesn't exclude it
- but **not** against a co-resident process or user with equivalent
filesystem access to your account (e.g. another process running as you,
or someone with root/backup access to the machine): anyone who can read
`credentials.json` can, in that scenario, also read `.master_key` and
decrypt it. If you suspect the key may have been exposed, rotate it with
`apiloop rotate-master-key`, which re-encrypts the whole store under a
new key.

## Testing

```bash
pytest tests/ -v
```

## License

MIT
