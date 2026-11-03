# Contributing

Keep generic features independent of MCU vendor/model. Add response-driven or mock-backed tests for behavior. Unknown capabilities must remain unknown unless actual evidence supports a value. Never claim hardware validation without recorded hardware test evidence. Run `ruff check .`, `mypy src/embedded_debug`, `pytest`, and `python skills/embedded-debug/scripts/validate_skill.py skills/embedded-debug` before a change. Destructive hardware tests must be opt-in and never run in default CI.
