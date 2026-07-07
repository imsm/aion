"""aion.store — the data layer.

One SQLite table (``entries``), append-only. Every function here is plain Python
callable without MCP, so the core logic is trivially testable. The MCP tools in
``aion.server`` are thin wrappers over these.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- configuration (env-driven; each MCP client sets these in its own config) ---
DB_PATH = os.environ.get("AION_DB", str(Path.home() / ".aion" / "aion.db"))
# Who is writing — declared by each client so every entry has a clean audit tag.
SOURCE = os.environ.get("AION_SOURCE", "agent")
# Default project segment. The first element of a dotted path IS the project.
DEFAULT_SEGMENT = os.environ.get("AION_SEGMENT", "project")

LOG_TYPES = {"decision", "change", "question", "note", "task"}
ROADMAP_KINDS = {"project", "segment", "phase", "epic", "task"}  # tree node kinds
VALID_TYPES = LOG_TYPES | ROADMAP_KINDS
PRIORITY_MIN, PRIORITY_MAX = 1, 5  # 1 = highest
HANDOFF_TARGETS = frozenset({"cursor", "claude-code", "claude-desktop"})
_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._/-]+$")


# --------------------------------------------------------------------------- #
# connection & schema
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=5.0)
    con.row_factory = sqlite3.Row
    # Many client processes share one file — WAL + a busy timeout keep concurrent
    # writes from failing with "database is locked".
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _has_fts5(con: sqlite3.Connection) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        con.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _migrate(con: sqlite3.Connection) -> None:
    """Additive, idempotent. Adds roadmap columns without touching existing rows."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(entries)")}
    if "parent_id" not in cols:
        con.execute("ALTER TABLE entries ADD COLUMN parent_id INTEGER")
    if "priority" not in cols:
        con.execute("ALTER TABLE entries ADD COLUMN priority INTEGER")
    con.execute("CREATE INDEX IF NOT EXISTS idx_parent ON entries(parent_id)")
    con.commit()


def init_db(db_path: str = DB_PATH) -> None:
    con = _connect(db_path)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL,
            segment TEXT NOT NULL,
            type    TEXT NOT NULL,
            content TEXT NOT NULL,
            refs    TEXT,
            status  TEXT NOT NULL DEFAULT 'open',
            source  TEXT NOT NULL DEFAULT 'human'
        );
        CREATE INDEX IF NOT EXISTS idx_seg  ON entries(segment);
        CREATE INDEX IF NOT EXISTS idx_type ON entries(type);
        """
    )
    _migrate(con)
    if _has_fts5(con):
        con.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
                USING fts5(content, segment, type, content='entries', content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS e_ai AFTER INSERT ON entries BEGIN
                INSERT INTO entries_fts(rowid, content, segment, type)
                VALUES (new.id, new.content, new.segment, new.type);
            END;
            CREATE TRIGGER IF NOT EXISTS e_ad AFTER DELETE ON entries BEGIN
                INSERT INTO entries_fts(entries_fts, rowid, content, segment, type)
                VALUES ('delete', old.id, old.content, old.segment, old.type);
            END;
            CREATE TRIGGER IF NOT EXISTS e_au AFTER UPDATE ON entries BEGIN
                INSERT INTO entries_fts(entries_fts, rowid, content, segment, type)
                VALUES ('delete', old.id, old.content, old.segment, old.type);
                INSERT INTO entries_fts(rowid, content, segment, type)
                VALUES (new.id, new.content, new.segment, new.type);
            END;
            """
        )
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _seg_clause(segment: str):
    """Match a segment and all of its descendants (segment/...)."""
    return "(segment = ? OR segment LIKE ?)", [segment, segment + "/%"]


def _parse_refs(refs):
    if isinstance(refs, dict):
        return refs
    if not refs:
        return {}
    try:
        return json.loads(refs)
    except (ValueError, TypeError):
        return {}


def _rows(cur) -> list[dict]:
    out = []
    for r in cur.fetchall():
        d = dict(r)
        if d.get("refs"):
            try:
                d["refs"] = json.loads(d["refs"])
            except (ValueError, TypeError):
                pass
        out.append(d)
    return out


def _sanitize_segment(segment: str) -> str:
    """Dotted-path charset only; reject control characters."""
    if not segment or not _SEGMENT_RE.fullmatch(segment):
        raise ValueError(f"invalid segment: {segment!r}")
    if any(ord(c) < 32 for c in segment):
        raise ValueError(f"segment contains control characters: {segment!r}")
    return segment


# --------------------------------------------------------------------------- #
# core: log / resolve / search / pull
# --------------------------------------------------------------------------- #

def log_entry(segment, etype, content, refs=None, source="human", db_path=DB_PATH) -> int:
    if etype not in VALID_TYPES:
        raise ValueError(f"type must be one of {sorted(VALID_TYPES)}, got {etype!r}")
    con = _connect(db_path)
    cur = con.execute(
        "INSERT INTO entries(ts, segment, type, content, refs, status, source) "
        "VALUES (?,?,?,?,?,?,?)",
        (_now(), segment, etype, content,
         json.dumps(refs) if refs else None, "open", source),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def resolve_entry(entry_id, db_path=DB_PATH) -> int:
    con = _connect(db_path)
    con.execute("UPDATE entries SET status='resolved' WHERE id=?", (entry_id,))
    con.commit()
    changed = con.total_changes
    con.close()
    return changed


def search_entries(query, segment=None, limit=20, db_path=DB_PATH) -> list[dict]:
    con = _connect(db_path)
    results: list[dict] = []
    if _has_fts5(con):
        try:
            sql = ("SELECT e.id,e.ts,e.segment,e.type,e.content,e.source "
                   "FROM entries_fts f JOIN entries e ON e.id = f.rowid "
                   "WHERE entries_fts MATCH ?")
            params = [query]
            if segment:
                sql += " AND (e.segment = ? OR e.segment LIKE ?)"
                params += [segment, segment + "/%"]
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            results = _rows(con.execute(sql, params))
        except sqlite3.OperationalError:
            results = []
    if not results:  # FTS5 absent or no match -> LIKE fallback
        sql = "SELECT id,ts,segment,type,content,source FROM entries WHERE content LIKE ?"
        params = ["%" + query + "%"]
        if segment:
            sql += " AND (segment = ? OR segment LIKE ?)"
            params += [segment, segment + "/%"]
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        results = _rows(con.execute(sql, params))
    con.close()
    return results


def pull_context(segment, limit=15, for_source=None, db_path=DB_PATH) -> dict:
    """Assemble the slice a tool needs to start work on `segment`: open tasks and
    questions, recent decisions/changes/notes, and this client's inbox."""
    con = _connect(db_path)
    where, p = _seg_clause(segment)

    def q(sql, extra=()):
        return _rows(con.execute(sql, tuple(p) + tuple(extra)))

    ctx = {
        "segment": segment,
        "open_tasks": q(
            f"SELECT id,ts,segment,content,source FROM entries WHERE {where} "
            "AND type='task' AND status='open' ORDER BY ts DESC LIMIT 10"),
        "open_questions": q(
            f"SELECT id,ts,segment,content,source FROM entries WHERE {where} "
            "AND type='question' AND status='open' ORDER BY ts DESC LIMIT 20"),
        "recent_decisions": q(
            f"SELECT id,ts,segment,content,source FROM entries WHERE {where} "
            "AND type='decision' ORDER BY ts DESC LIMIT ?", (limit,)),
        "recent_changes": q(
            f"SELECT id,ts,segment,content,refs,source FROM entries WHERE {where} "
            "AND type='change' ORDER BY ts DESC LIMIT ?", (limit,)),
        # notes, excluding directed-handoff notes (those surface in `inbox`)
        "recent_notes": q(
            f"SELECT id,ts,segment,content,source FROM entries WHERE {where} "
            "AND type='note' AND (refs IS NULL OR json_extract(refs,'$.to') IS NULL) "
            "ORDER BY ts DESC LIMIT ?", (limit,)),
    }
    con.close()
    ctx["inbox"] = pull_inbox(for_source, db_path) if for_source else []
    return ctx


# --------------------------------------------------------------------------- #
# directed handoffs & inbox
# --------------------------------------------------------------------------- #

def handoff_entry(segment, content, to, intent="notify", extra_refs=None,
                  source="human", db_path=DB_PATH) -> int:
    if to not in HANDOFF_TARGETS:
        raise ValueError(f"to must be one of {sorted(HANDOFF_TARGETS)}, got {to!r}")
    refs = {"to": to, "intent": intent}
    if extra_refs:
        refs.update(extra_refs)
    return log_entry(segment, "note", content, refs=refs, source=source, db_path=db_path)


def pull_inbox(for_source, db_path=DB_PATH) -> list[dict]:
    """Open handoffs addressed to `for_source` (refs.to). PENDING until resolved."""
    con = _connect(db_path)
    rows = _rows(con.execute(
        "SELECT id,ts,segment,type,content,refs,source FROM entries "
        "WHERE status='open' AND refs IS NOT NULL "
        "AND json_extract(refs, '$.to') = ? ORDER BY ts DESC LIMIT 50",
        (for_source,),
    ))
    con.close()
    out = []
    for r in rows:
        refs = _parse_refs(r.get("refs"))
        out.append({"id": r["id"], "from": r["source"], "segment": r["segment"],
                    "title": r["content"], "intent": refs.get("intent"),
                    "status": "PENDING"})
    return out


# --------------------------------------------------------------------------- #
# completion integrity — git is ground truth
# --------------------------------------------------------------------------- #

def _git_commit_state(sha: str, workspace: str | None = None) -> str:
    base = workspace or os.getcwd()
    try:
        if subprocess.run(["git", "-C", base, "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode != 0:
            return "missing"
        merged = subprocess.run(
            ["git", "-C", base, "merge-base", "--is-ancestor", sha, "HEAD"],
            capture_output=True).returncode == 0
        return "merged" if merged else "present-unmerged"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def completion_report(segment=None, db_path=DB_PATH) -> dict:
    """Classify each 'change' entry against git: a change with no git ref is a
    CLAIM, not "done". Checks commits in the repo the server runs in."""
    con = _connect(db_path)
    if segment:
        where, p = _seg_clause(segment)
    else:
        where, p = "1=1", []
    rows = _rows(con.execute(
        f"SELECT id,ts,segment,content,refs,source FROM entries "
        f"WHERE {where} AND type='change' ORDER BY ts DESC LIMIT 50", tuple(p)))
    con.close()
    changes, verified, claims = [], 0, 0
    for r in rows:
        refs = _parse_refs(r.get("refs"))
        sha, pr = refs.get("commit"), refs.get("pr")
        if sha:
            state = _git_commit_state(sha)
        elif pr:
            state = "pr-ref (unchecked)"
        else:
            state = "CLAIM (no git ref)"
        if state == "merged":
            verified += 1
        elif state.startswith("CLAIM"):
            claims += 1
        changes.append({"id": r["id"], "segment": r["segment"], "source": r["source"],
                        "content": r["content"][:110], "commit": sha, "pr": pr,
                        "verified": state})
    return {"segment": segment or "(all)", "verified_merged": verified,
            "bare_claims": claims, "changes": changes}


# --------------------------------------------------------------------------- #
# roadmap (hierarchy via parent_id; project > segment > phase > epic > task)
# --------------------------------------------------------------------------- #

def roadmap_add(kind, title, parent_id=None, segment="", priority=None,
                source="human", db_path=DB_PATH) -> int:
    if kind not in ROADMAP_KINDS:
        raise ValueError(f"kind must be one of {sorted(ROADMAP_KINDS)}, got {kind!r}")
    if priority is not None and not (PRIORITY_MIN <= int(priority) <= PRIORITY_MAX):
        raise ValueError(f"priority must be {PRIORITY_MIN}..{PRIORITY_MAX}")
    if not segment and parent_id:  # inherit the parent's segment
        con = _connect(db_path)
        row = con.execute("SELECT segment FROM entries WHERE id=?", (parent_id,)).fetchone()
        con.close()
        segment = row["segment"] if row else ""
    con = _connect(db_path)
    cur = con.execute(
        "INSERT INTO entries(ts, segment, type, content, status, source, parent_id, priority) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (_now(), segment, kind, title, "open", source, parent_id, priority),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def roadmap_set(entry_id, priority=None, parent_id=None, status=None, db_path=DB_PATH) -> int:
    sets, params = [], []
    if priority is not None:
        if not (PRIORITY_MIN <= int(priority) <= PRIORITY_MAX):
            raise ValueError(f"priority must be {PRIORITY_MIN}..{PRIORITY_MAX}")
        sets.append("priority=?")
        params.append(int(priority))
    if parent_id is not None:
        sets.append("parent_id=?")
        params.append(parent_id)
    if status is not None:
        sets.append("status=?")
        params.append(status)
    if not sets:
        return 0
    con = _connect(db_path)
    con.execute(f"UPDATE entries SET {','.join(sets)} WHERE id=?", params + [entry_id])
    con.commit()
    n = con.total_changes
    con.close()
    return n


def roadmap_link(blocked_id, blocker_id, db_path=DB_PATH) -> int:
    """Record a dependency edge: `blocked_id` is blocked_by `blocker_id`. Stored in
    refs, surfaced by roadmap_tree — distinct from parent_id (decomposition)."""
    con = _connect(db_path)
    row = con.execute("SELECT refs FROM entries WHERE id=?", (blocked_id,)).fetchone()
    if row is None:
        con.close()
        return 0
    refs = _parse_refs(row[0]) or {}
    refs["blocked_by"] = sorted(set(refs.get("blocked_by") or []) | {int(blocker_id)})
    con.execute("UPDATE entries SET refs=? WHERE id=?", (json.dumps(refs), blocked_id))
    con.commit()
    n = con.total_changes
    con.close()
    return n


def _rollup(node):
    """Recursively compute done/total. A leaf WORK node (task or epic) counts;
    resolved => done. Structural nodes (project/segment/phase) only aggregate."""
    kids = node.get("children", [])
    if not kids:
        countable = node["type"] in ("task", "epic")
        done = 1 if (countable and node["status"] == "resolved") else 0
        total = 1 if countable else 0
        node["done"], node["total"] = done, total
        return done, total
    done = total = 0
    for k in kids:
        d, t = _rollup(k)
        done += d
        total += t
    node["done"], node["total"] = done, total
    return done, total


def roadmap_tree(root_segment=None, db_path=DB_PATH) -> list:
    con = _connect(db_path)
    rows = _rows(con.execute(
        "SELECT id,parent_id,type,content,segment,status,priority,refs FROM entries "
        "WHERE type IN ('project','segment','phase','epic','task')"))
    con.close()
    by_id = {}
    for r in rows:
        node = {k: r[k] for k in ("id", "parent_id", "type", "content",
                                  "segment", "status", "priority")}
        rf = _parse_refs(r.get("refs"))
        if rf.get("blocked_by"):
            node["blocked_by"] = rf["blocked_by"]
        node["children"] = []
        by_id[r["id"]] = node
    roots = []
    for r in by_id.values():
        pid = r["parent_id"]
        (by_id[pid]["children"].append(r) if pid in by_id else roots.append(r))

    def sort_kids(n):
        n["children"].sort(key=lambda c: (c["priority"] is None, c["priority"] or 0, c["id"]))
        for c in n["children"]:
            sort_kids(c)

    for r in roots:
        sort_kids(r)
        _rollup(r)
    roots.sort(key=lambda c: (c["priority"] is None, c["priority"] or 0, c["id"]))
    if root_segment:
        roots = [r for r in roots if r["segment"] == root_segment
                 or r["segment"].startswith(root_segment + "/")]
    return roots


def roadmap_status(db_path=DB_PATH) -> dict:
    tree = roadmap_tree(db_path=db_path)
    per = [{"segment": n["segment"], "title": n["content"], "kind": n["type"],
            "priority": n["priority"], "done": n["done"], "total": n["total"],
            "pct": round(100 * n["done"] / n["total"]) if n["total"] else None}
           for n in tree]
    done = sum(n["done"] for n in tree)
    total = sum(n["total"] for n in tree)
    return {"nodes": per, "overall_done": done, "overall_total": total,
            "overall_pct": round(100 * done / total) if total else None}


# --------------------------------------------------------------------------- #
# constraints & cross-project reuse
# --------------------------------------------------------------------------- #

def _ancestors(segment):
    parts = [p for p in segment.split("/") if p]
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def decisions_for(segment, db_path=DB_PATH) -> dict:
    """Every decision + open question that may CONSTRAIN work on `segment`: the
    segment, its ancestors, and its descendants (not recency-capped)."""
    con = _connect(db_path)
    ancestors = _ancestors(segment)
    placeholders = ",".join("?" for _ in ancestors) or "''"
    decisions = _rows(con.execute(
        f"SELECT id,ts,segment,type,content,status FROM entries "
        f"WHERE type='decision' AND (segment IN ({placeholders}) "
        f"OR segment = ? OR segment LIKE ?) ORDER BY ts DESC",
        (*ancestors, segment, segment + "/%")))
    questions = _rows(con.execute(
        f"SELECT id,ts,segment,type,content,status FROM entries "
        f"WHERE type='question' AND status='open' AND (segment IN ({placeholders}) "
        f"OR segment = ? OR segment LIKE ?) ORDER BY ts DESC",
        (*ancestors, segment, segment + "/%")))
    con.close()
    return {"segment": segment, "decisions": decisions, "open_questions": questions}


def lookup_other_project(query, project, db_path=DB_PATH) -> list[dict]:
    """Search ONE other project's history for a topic, to reuse its approach."""
    return search_entries(query, segment=project, limit=20, db_path=db_path)


# --------------------------------------------------------------------------- #
# git ingest (used by the post-commit hook / the `log-commit` CLI command)
# --------------------------------------------------------------------------- #

def _git(*args) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def log_commit(db_path=DB_PATH) -> None:
    init_db(db_path)
    try:
        sha = _git("rev-parse", "--short", "HEAD")
        msg = _git("log", "-1", "--pretty=%s")
        files = [f for f in _git("diff-tree", "--no-commit-id", "--name-only",
                                 "-r", "HEAD").split("\n") if f]
    except Exception as e:  # noqa: BLE001
        print(f"aion: could not read git state: {e}", file=sys.stderr)
        return
    seg = DEFAULT_SEGMENT
    tops = {f.split("/", 1)[0] for f in files if "/" in f}
    if len(tops) == 1:  # all touched files share one top-level dir -> sub-segment hint
        seg = f"{DEFAULT_SEGMENT}/{next(iter(tops))}"
    rid = log_entry(seg, "change", f"commit {sha}: {msg}",
                    refs={"commit": sha, "files": files}, source="git", db_path=db_path)
    print(f"aion: logged commit {sha} as entry {rid} in {seg}")
