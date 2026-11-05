# Changelog

## 0.1.0 - Unreleased candidate

Initial implementation of the generic OpenOCD/GDB- MI architecture, system toolchain diagnostics, Skill files, and release-gate workflows. This version is not a stable release; the exact merged master candidate has physical STM32H743 HIL evidence, but no formal release-candidate acceptance record exists yet.

Reliability follow-up: bind GDB/MI reader threads to their process generation during reconnects, preserve primary HIL failures when session cleanup also encounters errors, and validate the exact merged master candidate on the STM32H743 HIL fixture.
