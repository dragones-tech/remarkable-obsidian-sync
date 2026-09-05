# Suggested first Codex task

Open this repository in Codex and use the following task:

> Implement and harden the MVP described in SPEC.md. Start by running the tests and reviewing all shell commands for safety. Keep the desktop client read-only with respect to `/home/root/.local/share/remarkable/xochitl/`. Add unit tests for metadata parsing, UUID validation, deterministic destination naming, fingerprinting, and state updates. Do not implement a xochitl binary patch yet. Next, add a pluggable renderer interface but keep rendering optional until a parser compatible with the actual firmware/file format has been selected and tested against sample data. Document every device-side write.

Before any real-device experiment, capture `uname -a`, `/etc/os-release` if present, the reMarkable software version, and a read-only listing of the relevant data directories.
