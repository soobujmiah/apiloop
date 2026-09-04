# APIloop Configuration Guide

## Configuration Files

### Primary Config: `~/.config/apiloop/config.yaml`

Human-readable YAML containing provider definitions, model registry, and routing policy.

```yaml
providers:
  openai:
    id: openai
    name: OpenAI
    kind: remote
    base_url: https://api.openai.com/v1
    requires_authentication: true
    authentication_method: bearer_token
    supports_streaming: true
    health: healthy

models:
  gpt-4o:
    id: gpt-4o
    provider_id: openai
    display_name: GPT-4o
    modalities: [text]
    capabilities: [chat, vision, embeddings]
    context_window: 128000
    streaming: true
    tool_calling: true
```

### Credentials: `~/.config/apiloop/credentials.json`

Encrypted file managed by `CredentialManager`. Never edit manually.

```bash
# Add credential via CLI (encrypts automatically)
apiloop provider credential add openai --api-key sk-...
```

## Provider Configuration

### Required Fields
- `id`: Unique identifier (e.g., `openai`, `ollama-local`)
- `name`: Human-readable name
- `kind`: `remote` or `local`
- `base_url`: API endpoint URL

### Optional Fields
- `requires_authentication`: Defaults to `false`
- `authentication_method`: `bearer_token`, `api_key`, or `none`
- `supports_streaming`: Defaults to `true`
- `health`: Auto-managed by system

### Example: Ollama (Local)
```yaml
providers:
  ollama:
    id: ollama
    name: Ollama Local
    kind: local
    base_url: http://localhost:11434/v1
    requires_authentication: false
```

### Example: Custom Provider
```yaml
providers:
  my-custom:
    id: my-custom
    name: My Custom API
    kind: remote
    base_url: https://api.example.com/v1
    requires_authentication: true
    authentication_method: api_key
```

## Model Configuration

### Required Fields
- `id`: Model identifier (matches provider's model ID)
- `provider_id`: Must reference existing provider
- `display_name`: Human-readable name

### Capabilities
Models can declare multiple capabilities:
- `chat`: Text completion/chat
- `vision`: Image understanding
- `embeddings`: Vector embeddings
- `image_generation`: Text-to-image
- `audio_transcription`: Speech-to-text

### Context Window
Specify approximate context length for routing decisions:
```yaml
models:
  my-model:
    context_window: 8192
```

## Routing Configuration

### Strategy Selection
Set default routing strategy in config:
```yaml
routing:
  strategy: health_aware  # or: round_robin, failover, quota_aware
  failover:
    priority_order: [provider-a, provider-b, provider-c]
```

### Retry Settings
```yaml
routing:
  max_retries: 3
  base_delay_ms: 1000
  backoff_factor: 2.0
```

## CLI Commands for Config Management

```bash
# View current configuration
apiloop doctor

# Add provider
apiloop provider add my-provider --type openai_compatible --base-url https://api.example.com/v1

# Add credential
apiloop provider credential add my-provider --api-key sk-...

# List models
apiloop model list

# Change routing strategy
apiloop route set --strategy health_aware
```

## Validation

Always validate after changes:
```bash
apiloop doctor --validate
```

This checks:
- Provider references exist
- Models link to valid providers
- Credentials are configured where required
- No circular dependencies
