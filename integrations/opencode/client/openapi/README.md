# OpenCode OpenAPI contract

`contract-v1.18.16.json` records the immutable source commit, byte count,
SHA-256 and the operations consumed by JARVIS. It was derived from
`packages/sdk/openapi.json` at OpenCode tag `v1.18.16`.

The complete 1.06 MB generated specification is not duplicated here. Contract
tests must fetch the immutable `source_url` only in an explicitly networked CI
job, verify `source_bytes` and `source_sha256`, then assert the listed operation
IDs and schemas. Standard unit tests remain offline.
