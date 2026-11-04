# Changelog

## 0.1.0 - Unreleased candidate

Initial implementation of the generic OpenOCD/GDB- MI architecture, system toolchain diagnostics, Skill files, and release-gate workflows. This version is not a stable release and does not claim physical hardware validation.

Reliability follow-up: bind GDB/MI reader threads to their process generation during reconnects, and preserve primary HIL failures when session cleanup also encounters errors.
