# APIloop Architecture

## Overview

APIloop is a provider-agnostic AI API gateway that provides unified access to multiple AI providers through intelligent routing and secure credential management.

## Core Components

### 1. Provider Abstraction Layer

```
Provider Interface (ABC)
├── Authentication
├── Model Discovery
├── Capability Discovery
├── Request Translation
├── Response Normalization
├── Streaming Support
├── Error Normalization
└── Health Checking
```

#### Adapters Implemented
- **OpenAICompatibleAdapter**: Handles OpenAI and OpenAI-compatible providers (Ollama, custom endpoints)
- **Pluggable architecture**: Easy to add new adapters

### 2. Credential Manager

Secure credential storage with:
- Fernet symmetric encryption
- Master key rotation
- Persistence across sessions
- Redaction in logs and outputs

#### Security Properties
- Credentials encrypted at rest
- No secrets in Git-tracked files
- Automatic redaction in error messages
- Restricted file permissions (0600)

### 3. Routing Engine

Four strategies available:

| Strategy | Behavior |
|----------|----------|
| `round_robin` | Cycles through healthy providers evenly |
| `failover` | Uses priority-ordered list, falls back on failure |
| `health_aware` | Selects healthiest available provider |
| `quota_aware` | Prioritizes providers with higher remaining quota |

#### Failure Handling
- HTTP 429 (Rate Limited): Cooldown + retry with backoff
- HTTP 5xx: Retry with exponential backoff
- Authentication failures: Mark provider unhealthy
- Connection errors: Failover to next provider

### 4. Model Registry

Tracks model capabilities including:
- Provider association
- Modalities (text, image, audio, video)
- Features (streaming, tools, vision, embeddings)
- Quota/availability information
- Pricing (when available)

### 5. Configuration System

```
~/.config/apiloop/
├── config.yaml          # Public configuration (providers, models, routing)
└── credentials.json     # Encrypted credentials (never committed)
```

## Data Flow

```
Client Request
    │
    ▼
FastAPI Gateway
    │
    ├── Capability Resolver ──► Validate request supported by provider
    │
    ├── Model Registry ────────► Find compatible models
    │
    ├── Routing Engine ────────► Select provider based on strategy
    │
    ├── Credential Manager ────► Retrieve encrypted credentials
    │
    ├── Provider Adapter ──────► Translate and execute request
    │
    ▼
Provider API
    │
    ▼
Normalized Response
```

## Extensibility

### Adding New Providers

1. Create adapter class inheriting from `ProviderAdapter`
2. Register with `ProviderAdapterFactory.register()`
3. Implement required methods:
   - `chat_completion()`
   - `health_check()`
   - `list_models()`
   - `get_auth_headers()`

### Adding Routing Strategies

1. Inherit from `BaseStrategy`
2. Implement `route()` method
3. Register with `RoutingEngine`

## Security Architecture

```
Credential Storage (encrypted)
        │
        ▼
Credential Manager (Fernet encryption)
        │
        ▼
Provider Adapter (redacts secrets)
        │
        ▼
External Requests (no secrets in logs)
```

### Secret Redaction Rules
- Bearer tokens: `Bearer sk-***` (first 4, last 4 visible)
- API keys: `sk-***key` (pattern-based)
- Error messages: Automatically sanitized before logging

## Future Roadmap

### Phase 2 (Post-MVP)
- Anthropic adapter
- Google Gemini adapter
- Ollama native adapter
- Image generation support
- Audio transcription
- Real-time streaming improvements

### Phase 3
- Web UI dashboard
- Rate limit monitoring
- Cost tracking
- Multi-tenant support
- Plugin system for custom adapters
