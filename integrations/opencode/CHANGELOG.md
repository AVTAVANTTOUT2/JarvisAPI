# OpenCode provider changelog

## Unreleased

- Pin OpenCode `1.18.16` with an explicit platform/architecture allowlist.
- Verify release SHA-256 before manual, link-free archive extraction.
- Confine configuration, state, logs, cache and temporary files to `.runtime/`.
- Add authenticated loopback process lifecycle and the typed v1.18.16 client.
- Add four least-privilege JARVIS agents and bounded tool output.
- Allow per-run local MCP configuration through a strictly allowlisted runtime overlay.
- Add deterministic offline tests for security, lifecycle, HTTP and SSE.
