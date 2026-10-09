# ORDAX Runtime

Canonical Windows/external-host ORDAX Runtime, device agent and specialized host adapters.

Migrated from the retired `washingtonmsdj/mcp-blender` repository at commit `ce31808f950e04207f028c5925cca2da17d18553` on 2026-10-05. It is historical provenance only and is not a product authority or runtime dependency.

`ordaxsystems/ordax-runtime` is the canonical Runtime repository. Migration code may recognize the retired `mcp-blender` remote and the previous `washingtonmsdj/ordax-runtime` owner only to repoint existing managed checkouts; neither old URL is a product authority or an architectural dependency. New installs clone the `ordaxsystems` repository directly.

Product remote action recovery reuses `ProductRemoteClient` and the existing
authenticated action-status endpoint. See [Product action recovery](docs/PRODUCT-ACTION-RECOVERY.md)
for request identity, transport limits, uncertain submission/completion and the
separate PostgreSQL cutover dependencies. Source version is owned exclusively by
`pyproject.toml`; source tests do not establish an installed or production release.
