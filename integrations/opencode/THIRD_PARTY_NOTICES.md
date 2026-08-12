# Third-party notices

## OpenCode

- Upstream: <https://github.com/anomalyco/opencode>
- Version: `1.18.16` (`v1.18.16`, published 2026-08-10)
- License: MIT; vendored text in `LICENSE`
- Upstream LICENSE Git blob: `6439474beed8e0271df9862eff97ffd70ec2464c`
- Release assets and SHA-256 digests: `release-manifest.json`
- OpenAPI source commit: `a3647eb025c7615159d417dcc49fc39fdaeba65b`
- OpenAPI SHA-256: `5bbd6493a1a488ef4294889341c896e420f814ecea95822100aaa9f3f95ab2d1`

Security review performed 2026-08-11:

- `GHSA-c83v-7274-4vgp` / `CVE-2026-22813` (critical), fixed in `1.1.10`.
- `GHSA-vxw4-wv6m-9hhh` / `CVE-2026-22812` (high), fixed in `1.0.216`.

The pinned version is newer than both fixed versions. JARVIS additionally
disables the Web UI, sharing, mDNS, additional CORS origins and automatic
updates; it binds the server to loopback and requires ephemeral Basic Auth.
The enforced `minimum_safe_version` is `1.1.10`, the higher semantic bound.
