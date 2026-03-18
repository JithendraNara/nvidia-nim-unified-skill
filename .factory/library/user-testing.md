# User Testing

Validation approach for NVIDIA NIM Unified Skill.

## Validation Surface

**CLI-first skill** - No web UI. Testing via bash commands and script execution.

### Test Surfaces

1. **Direct CLI** - python3 scripts/nim_router.py commands
2. **Python imports** - Import nim_router functions directly
3. **API Server** - HTTP endpoints when server is running

### Tools Used

- Bash commands (curl for API testing)
- Python unittest/pytest for unit tests
- Mocked NVIDIA API responses for offline testing

## Validation Concurrency

**Max concurrent validators: 2**

Rationale:
- CLI tests are lightweight, no browser/app overhead
- API server tests require port allocation
- Resource headroom: 8GB RAM, 4 cores usable
- 2 concurrent allows parallel CLI + API testing

## Test Fixtures

- Mock responses in `tests/fixtures/`
- Sample images in `tests/images/`
- Example configs in `references/`

## Coverage Target

80% line coverage for nim_router/ modules
