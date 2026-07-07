# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's
[security advisories](https://github.com/imsm/aion/security/advisories/new) rather than
a public issue. We aim to acknowledge reports within a few days.

## Security model

aion is a **local** MCP server. It stores whatever your AI tools log in a SQLite
database under `~/.aion/` (treat that file as project-internal data).

- **No network egress in the core.** The store, roadmap, handoff/inbox, and search
  features do not send data anywhere.
- **The optional doorbell** (`AION_DELIVERY=1`) opens a local `cursor://` deeplink to
  summon Cursor. The deeplink carries only a fixed template (segment + handoff id),
  never your content, and Cursor still requires you to confirm before it runs.
- aion executes code on your behalf as any MCP server does — it's small; read
  `src/aion/` before trusting it.

## Scope

This release ships the neutral core (store, roadmap, handoffs, doorbell). Autonomous
"delegate" execution is intentionally **not** part of the public package.
