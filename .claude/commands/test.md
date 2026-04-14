Run the project test suite from the current project root and analyze the results.

1. Run all tests:
```bash
uv run python -m pytest tests/ -v --tb=short 2>&1
```

2. Report clearly:
   - Total passed / failed / skipped
   - For each failure: which test, what assertion failed, likely cause
   - If all pass: confirm count and note if anything changed

3. If there are failures: investigate the root cause by reading the relevant source file and test, then suggest a fix. Do not just repeat the error message.
