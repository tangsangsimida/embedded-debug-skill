# Contributing

Keep generic features independent of MCU vendor/model. Add response-driven or mock-backed tests for behavior. Unknown capabilities must remain unknown unless actual evidence supports a value. Never claim hardware validation without recorded hardware test evidence.

Use an editable install for local development so `edbg` and Python imports resolve to this checkout:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

For fish, activate with `source .venv/bin/activate.fish`. On Windows, create/activate the venv with the platform's standard `py -3.11 -m venv .venv` and activation commands, then run the same pip and pytest commands. `tests/conftest.py` also pins pytest and integration-test child processes to this checkout's absolute `src` path and a test checks that import origin, preventing a stale installed wheel from masking source changes.

Run `ruff check .`, `mypy src/embedded_debug`, `python -m pytest`, and `python skills/embedded-debug/scripts/validate_skill.py skills/embedded-debug` before a change. CI continuously validates Python 3.11 across Linux, Windows, and macOS runner platforms; `pyproject.toml` declares Python 3.11 or newer, but newer interpreter versions are not currently part of the continuous compatibility claim. Platform-specific regressions belong in those OS runners. Destructive hardware tests must be opt-in and never run in default CI.
