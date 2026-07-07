"""aionai.server — the MCP surface.

Thin ``@mcp.tool`` / ``@mcp.prompt`` wrappers over :mod:`aionai.store` and
:mod:`aionai.delivery`. Each tool parses its arguments, calls one storage function,
and returns JSON. All the real logic lives in the store.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from . import store
from .delivery import deliver_handoff
from .store import DEFAULT_SEGMENT, SOURCE

mcp = FastMCP("aionai")


# --------------------------------------------------------------------------- #
# working state: pull / log / resolve / search / verify
# --------------------------------------------------------------------------- #

@mcp.tool()
def context_pull(segment: str = DEFAULT_SEGMENT) -> str:
    """Pull the current shared working state for a project segment (and its
    sub-segments). Call this FIRST, before doing anything on the project: it returns
    the open tasks, open questions, recent decisions/changes/notes, and your inbox
    of handoffs. Treat it as the current truth about where the work stands.

    `segment` is a dotted path, e.g. "myproject" or "myproject/backend/auth"; a
    parent pulls all descendants. Treat status entries ("fixed"/"done") as CLAIMS to
    verify against git (see context_verify), not facts."""
    store.init_db()
    return json.dumps(store.pull_context(segment, for_source=SOURCE), indent=2)


@mcp.tool()
def context_log(segment: str, type: str, content: str, refs: str = "") -> str:
    """Append an entry to the shared working state so other tools see it:
    type = decision | change | question | task | note. `refs` is optional JSON
    (e.g. {"files": ["..."], "commit": "abc123"}). Returns the new entry id.

    Store WHAT/WHY (facts, decisions, intent), not HOW (procedure). A `change` is
    "done" only when merged in git — include refs.commit/refs.pr; without a ref it
    is recorded as a CLAIM (verify with context_verify)."""
    store.init_db()
    parsed = None
    if refs:
        try:
            parsed = json.loads(refs)
        except ValueError:
            parsed = {"note": refs}
    try:
        rid = store.log_entry(segment, type, content, refs=parsed, source=SOURCE)
    except ValueError as e:
        return f"error: {e}"
    msg = f"logged entry {rid} ({type}) in {segment}"
    if type == "change" and not (parsed and (parsed.get("commit") or parsed.get("pr"))):
        msg += (" (no git ref — recorded as a CLAIM, not 'done'; add refs.commit once "
                "merged, check with context_verify)")
    return msg


@mcp.tool()
def context_resolve(entry_id: int) -> str:
    """Mark an open question or task resolved so it stops surfacing in context_pull."""
    store.init_db()
    n = store.resolve_entry(entry_id)
    return f"resolved entry {entry_id}" if n else f"no entry {entry_id}"


@mcp.tool()
def context_search(query: str, segment: str = "") -> str:
    """Full-text search the project's history (decisions/changes/questions/notes) to
    recover past context. FTS5 does not stem — add a prefix wildcard (auth*) if a
    bare term seems too narrow."""
    store.init_db()
    return json.dumps(store.search_entries(query, segment or None), indent=2)


@mcp.tool()
def context_verify(segment: str = "") -> str:
    """Completion integrity: list 'change' entries and classify each against GIT (the
    ground truth) — 'merged', 'present-unmerged', 'missing', or a bare 'CLAIM (no git
    ref)'. A tool logging "fixed" is NOT done; done = merged in git. Checks commits in
    the repo the server runs in."""
    store.init_db()
    return json.dumps(store.completion_report(segment or None), indent=2)


# --------------------------------------------------------------------------- #
# directed handoff (inbox + optional doorbell)
# --------------------------------------------------------------------------- #

@mcp.tool()
def context_handoff(segment: str, content: str, to: str, intent: str = "notify",
                    refs: str = "") -> str:
    """Post a handoff to another tool's inbox (to = cursor | claude-code |
    claude-desktop). It lands in the target's inbox and stays PENDING until the
    receiver resolves it. If AIONAI_DELIVERY is enabled and the target has a verified
    deeplink (today: cursor), a doorbell also summons that tool with a fixed nudge —
    the URL never carries your content. Returns the entry id and delivery outcome."""
    store.init_db()
    extra = None
    if refs:
        try:
            extra = json.loads(refs)
        except ValueError:
            extra = {"note": refs}
    try:
        rid = store.handoff_entry(segment, content, to, intent=intent,
                                  extra_refs=extra, source=SOURCE)
    except ValueError as e:
        return f"error: {e}"
    outcome = deliver_handoff(to, segment, rid)
    return json.dumps({"entry_id": rid, "to": to, **outcome})


# --------------------------------------------------------------------------- #
# roadmap
# --------------------------------------------------------------------------- #

@mcp.tool()
def roadmap_add_node(kind: str, title: str, parent_id: int = 0,
                     segment: str = "", priority: int = 0) -> str:
    """Add a node to the roadmap tree. kind = project | segment | phase | epic | task.
    parent_id nests under another node (0 = top-level); a child inherits its parent's
    segment unless set. priority is 1..5 (1 = highest; 0 = none). Returns the id."""
    store.init_db()
    try:
        rid = store.roadmap_add(kind, title, parent_id=parent_id or None,
                                segment=segment, priority=priority or None, source=SOURCE)
    except ValueError as e:
        return f"error: {e}"
    return f"added {kind} node {rid}: {title}"


@mcp.tool()
def roadmap_update(entry_id: int, priority: int = 0, parent_id: int = 0,
                   status: str = "") -> str:
    """Update a roadmap node: set priority (1..5), reparent (parent_id), or set status
    ('open'/'resolved'). Pass only what you want to change (0/empty = leave as is).
    Resolving a task/epic is what makes progress roll up."""
    store.init_db()
    try:
        n = store.roadmap_set(entry_id, priority=priority or None,
                              parent_id=parent_id or None, status=status or None)
    except ValueError as e:
        return f"error: {e}"
    return f"updated node {entry_id}" if n else f"no change to {entry_id}"


@mcp.tool()
def roadmap_block(blocked_id: int, blocked_by_id: int) -> str:
    """Record a DEPENDENCY edge: node `blocked_id` is blocked by `blocked_by_id`. Use
    this for real dependencies instead of overloading parent_id (parent_id =
    decomposition). Surfaced in roadmap_view as a node's `blocked_by`."""
    store.init_db()
    n = store.roadmap_link(blocked_id, blocked_by_id)
    return (f"edge recorded: node {blocked_id} blocked_by {blocked_by_id}"
            if n else f"no node {blocked_id}")


@mcp.tool()
def roadmap_view(segment: str = "") -> str:
    """Show the hierarchical roadmap (project > segment > phase > epic > task) with
    done/total rollup per node, sorted by priority. Optionally scope to a segment."""
    store.init_db()
    return json.dumps(store.roadmap_tree(segment or None), indent=2)


@mcp.tool()
def roadmap_progress() -> str:
    """Progress summary: per top-level segment and overall, how many leaf work nodes
    are done vs total, with percentages."""
    store.init_db()
    return json.dumps(store.roadmap_status(), indent=2)


# --------------------------------------------------------------------------- #
# constraints & cross-project reuse
# --------------------------------------------------------------------------- #

@mcp.tool()
def constraints_for_task(segment: str) -> str:
    """Before implementing a task, get every decision and open question that may
    CONSTRAIN it — from the task's segment, its ancestors, and its descendants
    (not recency-limited). Surface any conflict with your plan BEFORE coding."""
    store.init_db()
    return json.dumps(store.decisions_for(segment), indent=2)


@mcp.tool()
def project_lookup(query: str, project: str) -> str:
    """Reuse an approach from ANOTHER project: search that project's history for a
    topic and adapt the matching decisions/notes instead of starting from scratch.
    FTS5 does not stem — add a prefix wildcard (auth*) if a bare term is too narrow."""
    store.init_db()
    return json.dumps(store.lookup_other_project(query, project), indent=2)


# --------------------------------------------------------------------------- #
# prompts (surface as slash commands, e.g. /mcp__aionai__pull)
# --------------------------------------------------------------------------- #

@mcp.prompt(title="aionai: pull working state")
def pull(segment: str = "") -> str:
    """Pull current aionai working state for a segment (default: whole project)."""
    seg = f"{DEFAULT_SEGMENT}/{segment}" if segment else DEFAULT_SEGMENT
    return (f"Call the aionai MCP tool context_pull with segment=\"{seg}\". Then give a "
            "tight readout of open tasks, open questions, recent decisions/changes, and "
            "your inbox. Do not paste raw JSON.")


@mcp.prompt(title="aionai: log an entry")
def log(entry: str = "") -> str:
    """Log a decision/change/question/task/note to aionai."""
    src = f'Log this: "{entry}".' if entry else "Log what we just discussed."
    return (f"Call the aionai MCP tool context_log. {src} Choose the correct type and the "
            f"most specific segment under \"{DEFAULT_SEGMENT}\". One concise line; include "
            "refs (files/commit) when relevant. Report the entry id.")


@mcp.prompt(title="aionai: resolve an entry")
def resolve(entry_id: str = "") -> str:
    """Close an open aionai question or task by id."""
    if not entry_id:
        return "Ask me which aionai entry id to resolve, then call context_resolve with it."
    return (f"Call the aionai MCP tool context_resolve with entry_id={entry_id}. "
            "Confirm it resolved.")


@mcp.prompt(title="aionai: search history")
def search(query: str = "") -> str:
    """Full-text search aionai history."""
    return (f"Call the aionai MCP tool context_search with query=\"{query}\". Summarize the "
            "matches tightly (ids, segments, one line each).")


@mcp.prompt(title="aionai: hand off to another tool")
def handoff(to: str = "cursor", segment: str = "", content: str = "") -> str:
    """Post a handoff to another tool's inbox (and optional doorbell)."""
    seg = f"{DEFAULT_SEGMENT}/{segment}" if segment else DEFAULT_SEGMENT
    body = f'content="{content}"' if content else "the plan we just discussed"
    return (f"Call the aionai MCP tool context_handoff with segment=\"{seg}\", to=\"{to}\", "
            f"{body}. Report the entry id and whether the doorbell fired or it was inbox-only.")
