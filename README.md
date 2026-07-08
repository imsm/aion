# aionai

**A shared working-state layer for your AI coding tools.** Claude, Cursor, and other
MCP clients read and write one small notebook, so they stay on the same page — and
you stop being the copy-paste bus between them.

[![PyPI](https://img.shields.io/pypi/v/aionai.svg)](https://pypi.org/project/aionai/)
[![CI](https://github.com/imsm/aionai/actions/workflows/ci.yml/badge.svg)](https://github.com/imsm/aionai/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/aionai.svg)](https://pypi.org/project/aionai/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[![Add aionai to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/install-mcp?name=aionai&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyJhaW9uYWkiXX0=)

> One-click install for Cursor (it configures `uvx aionai`). After it's added, set
> `AIONAI_SOURCE=cursor` in the server's env. If Cursor can't find `uvx`, see
> [Troubleshooting](#troubleshooting); or set it up manually via
> [Connect your tools](#connect-your-tools).

---

## The problem

You use more than one AI assistant on a project. They don't know what each other did:
you tell Claude a decision, switch to Cursor, and Cursor has no idea. You end up
re-explaining and copy-pasting context by hand.

## What aionai does

aionai is a tiny local MCP server that keeps a **shared, persistent notebook** every
tool can read and write. It does three jobs:

- **Remember** — decisions, changes, questions, and notes, organized by project.
- **Track** — a lightweight roadmap/to-do list with progress rollup.
- **Hand off** — post a task to another tool's inbox (with an optional "doorbell").

It does **not** replace your repo, docs, or git — those stay the source of truth. aionai
just holds the live *working state* and hands each tool the slice it needs.

## Install

Most portable — works on Windows, macOS, and Linux, and puts the `aionai` command
where GUI apps (Cursor, Claude Desktop) can find it:

```bash
pipx install aionai
```

Already use [uv](https://docs.astral.sh/uv/)? Skip the install entirely:

```bash
uvx aionai --help
```

Requires **Python 3.10+**. If a client later reports the launcher (`aionai` / `uvx`)
"not recognized", see [Troubleshooting](#troubleshooting).

## Connect your tools

Point each client at the `aionai` command. Give each client a distinct `AIONAI_SOURCE`
(so handoffs route correctly) and set `AIONAI_SEGMENT` to your project name.

**Claude Code**

```bash
claude mcp add aionai --env AIONAI_SOURCE=claude-code --env AIONAI_SEGMENT=myproject -- aionai
# or with uv:  claude mcp add aionai --env AIONAI_SOURCE=claude-code -- uvx aionai
```

**Cursor** — `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "aionai": { "command": "aionai", "env": { "AIONAI_SOURCE": "cursor", "AIONAI_SEGMENT": "myproject" } }
  }
}
```

**Claude Desktop** — Settings → Developer → Edit Config:

```json
{
  "mcpServers": {
    "aionai": { "command": "aionai", "env": { "AIONAI_SOURCE": "claude-desktop", "AIONAI_SEGMENT": "myproject" } }
  }
}
```

> Restart the client after editing its config — MCP servers are launched (and their
> env read) when the client connects.

## Use it

Add this to each tool's rules (`CLAUDE.md`, Cursor rules) so they do it reflexively:

> Before working, call `context_pull(segment="myproject")` and treat the result as the
> current truth. As you work, `context_log(...)` your decisions/changes/questions/tasks.
> When something is settled, `context_resolve(id)`.

That's the whole loop: **pull first, write back.** Now open Cursor and it already knows
what you and Claude decided — no paste.

## Segments

State is organized by a dotted **segment** path whose first element is the project:

```
myproject                     # the whole project
myproject/backend             # a layer
myproject/backend/auth        # a workstream
```

Pulling a parent includes all descendants. That one mechanism keeps an always-on
space from turning into an undifferentiated blob.

## The tools

| tool | purpose |
|------|---------|
| `context_pull(segment)` | current working state + your inbox |
| `context_log(segment, type, content, refs)` | append a decision/change/question/task/note |
| `context_resolve(id)` | close a question or task |
| `context_search(query, segment)` | full-text recall over history |
| `context_verify(segment)` | check `change` entries against git (merged vs. bare claim) |
| `context_handoff(segment, content, to)` | post a handoff to another tool's inbox |
| `roadmap_add_node` / `roadmap_update` / `roadmap_block` | build & manage the roadmap |
| `roadmap_view` / `roadmap_progress` | see the tree / how far along |
| `constraints_for_task(segment)` | decisions + open questions that constrain a task |
| `project_lookup(query, project)` | reuse an approach from another project |

They also surface as slash commands (`/mcp__aionai__pull`, `…log`, `…resolve`, `…search`,
`…handoff`) in clients that support MCP prompts.

## Optional extras

- **Auto-ingest commits** — copy `hooks/post-commit` into a repo's `.git/hooks/`
  (set `AIONAI_SEGMENT`) and every commit logs itself as a `change`.
- **Doorbell** — set `AIONAI_DELIVERY=1` in a sender's env and a `context_handoff` to
  Cursor also *summons* Cursor via its deeplink (you confirm before it runs). Without
  it, handoffs are inbox-only. Only `cursor` has a verified deeplink today.

## How it works

One SQLite database, one append-only table. Every decision, task, handoff, and
roadmap node is a row tagged with a `segment` and a `type`. History is free because
nothing is overwritten. Full-text search uses FTS5 with a `LIKE` fallback. The MCP
tools are thin wrappers over a plain-Python storage layer (`src/aionai/store.py`).

## Troubleshooting

**A client reports `'uvx'` / `'aionai'` is not recognized (or the server errors on start).**
The client can't find the launcher on its PATH — common for GUI apps (Cursor, Claude
Desktop) on **Windows and macOS**, which don't always inherit your shell's PATH. Fixes,
best first:

1. **Use pipx:** `pipx install aionai`, then set `"command": "aionai"`. pipx puts the
   command where GUI apps usually find it.
2. **Point at the full path** of the launcher. Find it with `where uvx` (Windows) or
   `which uvx` (macOS/Linux), then use it verbatim, e.g.:
   ```json
   "aionai": { "command": "C:/Users/you/AppData/Roaming/Python/Python3xx/Scripts/uvx.exe",
               "args": ["aionai"], "env": { "AIONAI_SOURCE": "cursor" } }
   ```
3. **Skip the launcher** — run via your Python directly (after `pip install aionai`):
   ```json
   "aionai": { "command": "python", "args": ["-m", "aionai.cli"] }
   ```

Then **restart the client** so it re-reads the config.

## Configuration

| var | default | purpose |
|-----|---------|---------|
| `AIONAI_DB` | `~/.aionai/aionai.db` | database path (share explicitly if clients don't hit the default) |
| `AIONAI_SOURCE` | `agent` | who is writing (`cursor` / `claude-code` / `claude-desktop`); **routes the inbox** |
| `AIONAI_SEGMENT` | `project` | default project segment |
| `AIONAI_DELIVERY` | *(unset)* | `1` enables the doorbell in the sender |

## Development

```bash
git clone https://github.com/imsm/aionai && cd aionai
pip install -e ".[dev]"
pytest
ruff check .
```

## License

[Apache-2.0](LICENSE) © Ismail Saleh.
