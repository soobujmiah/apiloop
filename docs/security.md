# APIloop Security Model

## Secret Management

### Credential Storage
- All API keys and tokens are stored encrypted using Fernet (symmetric encryption)
- Encrypted credentials stored in `~/.config/apiloop/credentials.json` (owner-only permissions: 0600)
- Master encryption key generated per-installation, stored in
  `~/.config/apiloop/.master_key` (owner-only permissions: 0600) - **in the
  same directory as the ciphertext it protects**. This means the "0600 +
  encryption" mitigation below protects against accidental disclosure
  (git, casual snooping, an unfiltered backup), not against a co-resident
  process or user with equivalent access to your account - see "What
  APIloop Does NOT Protect Against" below. Rotate the master key with
  `apiloop rotate-master-key` if you suspect exposure.

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
| Accidental disclosure of stored keys (git, casual snooping, unfiltered backups) | File permissions (0600) + encryption at rest |
| Accidental exposure in error messages | Provider-level sanitization |
| Key reuse across providers | Provider-scoped credential IDs |

### What APIloop Does NOT Protect Against
- Provider-side API key compromise
- Network interception (use TLS/HTTPS)
- Malware on local machine
- Physical device access
- A co-resident process or user with equivalent access to your account:
  the master key sits in the same directory as the encrypted store it
  protects, so anyone who can read `credentials.json` can, in that
  scenario, also read `.master_key` and decrypt it

## Security Best Practices

1. **Never commit `.env` or real credentials**
2. **Rotate the master key** with `apiloop rotate-master-key` if you
   suspect exposure (per-provider credential rotation isn't yet exposed
   as a CLI command - remove and re-add the credential in the meantime)
3. **Use strong master keys** (auto-generated, don't override)
4. **Back up `credentials.json` securely** if migrating systems - and back
   up `.master_key` too, or the backup is useless
5. **Audit provider access** via `apiloop doctor` (`apiloop --verbose doctor`
   for debug-level logging)

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
