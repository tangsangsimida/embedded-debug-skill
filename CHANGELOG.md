# Changelog

## 0.1.2 - 2026-11-05

Release follow-up:

- record current STM32H743 HIL provenance and safe snapshot-analysis evidence;
- keep the release artifact and gated validation record aligned with the tagged commit.

## 0.1.1 - 2026-11-05

Reliability and release follow-up for the embedded debugging Skill:

- persist optional raw CLI evidence artifacts alongside provenance records;
- retain bounded GDB/MI queues across reconnects;
- distinguish validated HardFault snapshots from inferred root causes;
- document the STM32H743 GDB/MI HIL validation and diagnostic playbooks;
- enforce the release artifact layout and evaluation evidence contract.

## 0.1.0 - Unreleased candidate

Initial implementation of the generic OpenOCD/GDB- MI architecture, system toolchain diagnostics, Skill files, and release-gate workflows. This version is not a stable release; the exact merged master candidate has physical STM32H743 HIL evidence, but no formal release-candidate acceptance record exists yet.

Reliability follow-up: bind GDB/MI reader threads to their process generation during reconnects, preserve primary HIL failures when session cleanup also encounters errors, and validate the exact merged master candidate on the STM32H743 HIL fixture.
