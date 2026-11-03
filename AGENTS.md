# Repository instructions

- Keep target support OpenOCD-driven; do not add vendor/model allowlists or hardcoded chip maps.
- Preserve unknown capability states unless actual backend responses prove support.
- Do not modify or install host system toolchains as part of project setup.
- Hardware access is read-only by default; destructive tests are explicit opt-in.
- Formal releases are blocked until `ci.yml`, `skill-validation.yml`, and `skill-install-test.yml` pass on the exact release candidate.
