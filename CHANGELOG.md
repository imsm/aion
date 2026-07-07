# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-07-07

### Added
- Initial public release.
- Shared working-state store over MCP: `context_pull`, `context_log`,
  `context_resolve`, `context_search`, `context_verify`.
- Hierarchical roadmap with progress rollup, priorities, and dependency edges
  (`roadmap_add_node`, `roadmap_update`, `roadmap_block`, `roadmap_view`,
  `roadmap_progress`).
- Directed handoffs (inbox routed by `AIONAI_SOURCE`) with an optional, dark-by-default
  Cursor "doorbell" deeplink (`context_handoff`, `AIONAI_DELIVERY`).
- Constraint lookup (`constraints_for_task`) and cross-project reuse (`project_lookup`).
- Git commit auto-ingest via the `post-commit` hook and `aionai log-commit`.
- SQLite storage with FTS5 full-text search (and a `LIKE` fallback), WAL journaling,
  and additive, idempotent migrations.
