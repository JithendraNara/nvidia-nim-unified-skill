---
name: backend-worker
description: Python backend worker for CLI tools and API servers
---

# Backend Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Features involving Python CLI tools, API servers, or backend services. This skill handles:
- CLI command implementation
- Async execution patterns
- REST API development with FastAPI
- Retry/circuit breaker logic
- Caching layers
- Observability/logging

## Work Procedure

### 1. Read Context
- Read `mission.md` to understand feature requirements
- Read current implementation in `nim_router.py` and related files
- Read `AGENTS.md` for constraints and conventions

### 2. Write Tests First (TDD)
Before writing any implementation code:
- Create `tests/test_<feature_name>.py` with failing test cases
- Tests must fail against current code (red)
- Cover the expected behavior listed in features.json

Example test structure:
```python
import pytest
from nim_router import async_engine

@pytest.mark.asyncio
async def test_parallel_execution():
    """Async batch should be faster than sequential."""
    # Arrange
    urls = ["url1", "url2", "url3"]
    # Act
    start = time.time()
    results = await async_engine.invoke_batch(urls, async_mode=True)
    elapsed = time.time() - start
    # Assert
    assert len(results) == 3
    assert elapsed < 3.0  # Should be parallel, not 3x sequential
```

### 3. Implement Feature
- Implement in the appropriate module under `nim_router/`
- Follow coding conventions in AGENTS.md
- Add type hints and docstrings
- Maintain backward compatibility with original CLI

### 4. Verify Implementation
- Run tests: `pytest tests/ -v`
- Run typecheck: `python3 -m py_compile`
- Verify CLI still works: `python3 scripts/nim_router.py plan --task-query "extract text"`

### 5. Manual Verification
For each expectedBehavior item in features.json:
- Execute the specific command or scenario
- Record the actual output/behavior
- Confirm it matches expected behavior

## Example Handoff

```json
{
  "salientSummary": "Implemented async execution engine with parallel batch processing. Added --async flag that processes multiple images concurrently. All tests pass including new async tests.",
  "whatWasImplemented": "Async execution engine in nim_router/async_engine.py supporting parallel invocation of capabilities. Uses asyncio.gather for concurrent HTTP requests. New --async flag on invoke command enables parallel mode.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {
        "command": "pytest tests/test_async_engine.py -v",
        "exitCode": 0,
        "observation": "4 tests passed: test_parallel_execution, test_async_errors_dont_block, test_sequential_fallback, test_timing"
      },
      {
        "command": "python3 scripts/nim_router.py plan --task-query 'extract text'",
        "exitCode": 0,
        "observation": "Returns {primary_capability: 'ocr', workflow: ['ocr']} - unchanged from original"
      },
      {
        "command": "python3 scripts/nim_router.py --async invoke --capability ocr --image-url url1 --image-url url2",
        "exitCode": 0,
        "observation": "Both images processed in parallel, results returned as list"
      }
    ],
    "interactiveChecks": []
  },
  "tests": {
    "added": [
      {
        "file": "tests/test_async_engine.py",
        "cases": [
          {"name": "test_parallel_execution", "verifies": "VAL-ASYNC-001"},
          {"name": "test_async_errors_dont_block", "verifies": "VAL-ASYNC-002"}
        ]
      }
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Implementation requires API changes that break backward compatibility
- NVIDIA API behavior differs from documentation
- Feature scope significantly exceeds original estimate
- Missing dependencies that can't be resolved
