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

## Flow Validator Guidance: CLI Skill Validation

This is a CLI-first skill. No web UI or browser needed.

### Isolation Rules
- Tests operate on local files only (SKILL.md, scripts/nim_router.py)
- No shared state between validators
- Each assertion group can run independently

### Testing Approach
1. **YAML Parsing**: Verify SKILL.md parses as valid YAML
2. **Frontmatter Structure**: Check required fields for each platform
3. **Script Paths**: Verify {baseDir} is used correctly
4. **CLI Commands**: Test nim_router.py plan/build-request commands

### Evidence Collection
- Save parsed YAML output as evidence
- Save script output as evidence
- Create screenshots only if visual inspection needed (not applicable here)

### Platform-Specific Notes
- **OpenClaw**: Tests YAML metadata.openclaw structure
- **Claude Code**: Tests frontmatter single-line format and invocation docs
- **Codex**: Tests metadata.name/description fields
- **Routing**: Tests nim_router.py CLI interface

## Test Fixtures

- Mock responses in `tests/fixtures/`
- Sample images in `tests/images/`
- Example configs in `references/`

## Coverage Target

80% line coverage for nim_router/ modules
