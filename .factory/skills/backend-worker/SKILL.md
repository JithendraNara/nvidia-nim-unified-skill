---
name: backend-worker
description: Python backend worker for CLI tools and API servers
---

# Backend Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Features involving Python CLI tools, Python scripting, or data processing. This skill handles:
- CLI command implementation
- API client code
- Data processing and chunking
- Output formatting

## Work Procedure

### 1. Read Context
- Read `mission.md` to understand feature requirements
- Read existing `nim_router.py` structure
- Understand current capabilities

### 2. Write Tests First (TDD)
Create `tests/test_<feature>.py` with failing tests before implementation.

### 3. Implement Feature
- Add to appropriate module under `scripts/nim_router/`
- Follow existing code style
- Add type hints and docstrings

### 4. Verify
- Run tests: `pytest tests/ -v`
- Typecheck: `python3 -m py_compile`
- Manual test with real API if available

## Example Handoff

```json
{
  "salientSummary": "Implemented embed capability and semantic chunker",
  "whatWasImplemented": "Added embed.py with text embedding NIM support, chunker.py with semantic boundary detection",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "pytest tests/test_embed.py -v", "exitCode": 0, "observation": "3 tests passed"}
    ]
  },
  "tests": {"added": [{"file": "tests/test_embed.py", "cases": [{"name": "test_build_embed_request"}]}]},
  "discoveredIssues": []
}
```
