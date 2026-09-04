# APIloop Security Model

## Secret Management

### Credential Storage
- All API keys and tokens are stored encrypted using Fernet (symmetric encryption)
- Master encryption key generated per-installation
- Keys stored in `~/.config/apiloop/credentials.json` (owner-only permissions: 0600)

### Redaction Policy
Secrets are redacted in:
- Log output (API keys become `sk-***`)
- Error messages (Bearer tokens sanitized)
- JSON serialization (`model_dump()`)
- CLI output (status display)

Never exposed:
- Full API keys in any output
- Raw tokens in error traces
- Secrets in Git history

## Threat Model

### What APIloop Protects Against
| Threat | Mitigation |
|--------|-----------|
| Credential theft via logs | Automatic redaction in all output |
| Unauthorized access to stored keys | File permissions (0600) + encryption at rest |
| Accidental exposure in error messages | Provider-level sanitization |
| Key reuse across providers | Provider-scoped credential IDs |

### What APIloop Does NOT Protect Against
- Provider-side API key compromise
- Network interception (use TLS/HTTPS)
- Malware on local machine
- Physical device access

## Security Best Practices

1. **Never commit `.env` or real credentials**
2. **Rotate keys regularly** using `apiloop provider rotate`
3. **Use strong master keys** (auto-generated, don't override)
4. **Back up `credentials.json` securely** if migrating systems
5. **Audit provider access** via `apiloop doctor --verbose`

## Authentication Methods

Supported methods:
- `bearer_token` (OpenAI, Nara Router, most providers)
- `api_key` (Anthropic, custom headers)
- `none` (local providers like Ollama without auth)

## Validation

Configuration validation catches:
- Missing required credentials
- Invalid provider configurations
- Orphaned model references
- Broken routing policies

Run `apiloop doctor` for full security audit.
