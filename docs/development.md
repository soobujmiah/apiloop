# APIloop Development Guide

## Project Structure

```
apiloop/
├── src/apiloop/
│   ├── __init__.py          # Package init, version
│   ├── models.py            # Core data models
│   ├── config.py            # Configuration management
│   ├── cli.py               # CLI interface
│   ├── credentials/
│   │   └── manager.py       # Encrypted credential storage
│   ├── providers/
│   │   ├── base.py          # Provider interface & factory
│   │   └── openai_compatible.py  # OpenAI-style adapter
│   ├── routing/
│   │   └── engine.py        # Routing strategies
│   └── api/
│       └── app.py           # FastAPI application
├── tests/
│   ├── test_models.py       # Model unit tests
│   ├── test_credentials.py  # Credential manager tests
│   ├── test_routing.py      # Routing strategy tests
│   ├── test_openai_adapter.py  # Adapter tests
│   ├── test_security.py     # Security/redaction tests
│   └── test_config.py       # Configuration tests
├── docs/
├── pyproject.toml
└── README.md
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_credentials.py -v

# Run with coverage
pytest tests/ --cov=src/apiloop --cov-report=term-missing

# Run async tests specifically
pytest tests/test_routing.py -v -k "asyncio"
```

## Adding a New Provider Adapter

### Step 1: Create Adapter Class

```python
# src/apiloop/providers/my_provider.py
from .base import ProviderAdapter, ProviderAdapterFactory

@ProviderAdapterFactory.register("my-provider")
class MyProviderAdapter(ProviderAdapter):
    async def chat_completion(self, request):
        # Translate request to provider format
        # Make HTTP call
        # Normalize response
        pass
    
    async def health_check(self):
        # Return (is_healthy: bool, message: str)
        pass
    
    async def list_models(self):
        # Return list of ModelDescriptor
        pass
    
    def get_auth_headers(self):
        # Return auth headers dict
        pass
```

### Step 2: Add Tests

```python
# tests/test_my_provider.py
import pytest
from apiloop.providers.my_provider import MyProviderAdapter

@pytest.mark.asyncio
async def test_health_check():
    adapter = ...
    healthy, msg = await adapter.health_check()
    assert isinstance(healthy, bool)
```

### Step 3: Register in Factory

The decorator `@ProviderAdapterFactory.register()` handles registration automatically.

## Testing Patterns

### Mocking HTTP Requests

```python
from unittest.mock import AsyncMock, patch

@patch('apiloop.providers.openai_compatible.httpx.AsyncClient')
async def test_chat_completion(MockClient):
    mock_client = AsyncMock()
    mock_response = MagicMock(status_code=200, aread=AsyncMock(return_value=b'{}'))
    mock_client.post = AsyncMock(return_value=mock_response)
    MockClient.return_value = mock_client
    # ... test code
```

### Testing Encryption

```python
def test_encryption_roundtrip(tmp_path):
    cm = CredentialManager(storage_path=tmp_path / "creds.json")
    cm.add_credential("test", "default", "secret123")
    
    cm2 = CredentialManager(storage_path=tmp_path / "creds.json")
    cred = cm2.get_credential("test")
    assert cred.provider_id == "test"
```

## Code Style

- Follow PEP 8
- Type hints required for all public APIs
- Docstrings for all classes and methods
- Maximum line length: 100 characters

## Commit Convention

```
feat: add anthropic provider adapter
fix: handle 429 rate limiting in router
docs: update architecture diagram
test: add coverage for quota-aware routing
```

## Deployment

### Local Development
```bash
pip install -e ".[dev]"
apiloop doctor
```

### Production
```bash
pip install apiloop
# Or use Docker (future)
docker run -p 8080:8080 apiloop/server
```

## Performance Considerations

- Use connection pooling in adapters
- Cache model metadata where possible
- Async I/O for all network operations
- Stream responses when supported

## Troubleshooting

### Common Issues

1. **"No module named 'apiloop'"**
   ```bash
   pip install -e .
   ```

2. **"Master key not found"**
   First run will auto-generate. Re-run if missing.

3. **Provider health checks failing**
   Verify base_url and credentials with `apiloop provider test <name>`

## Future Enhancements

- [ ] Web UI for configuration
- [ ] Metrics export (Prometheus)
- [ ] Rate limit dashboards
- [ ] Cost tracking per request
- [ ] Plugin system for custom adapters
- [ ] Docker image for deployment
