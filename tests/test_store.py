"""Tests for the aion storage layer (no MCP required)."""

import pytest

from aion import store


@pytest.fixture()
def db(tmp_path):
    p = str(tmp_path / "aion-test.db")
    store.init_db(p)
    return p


def test_log_pull_resolve(db):
    rid = store.log_entry("proj", "task", "wire the endpoint", db_path=db)
    ctx = store.pull_context("proj", db_path=db)
    assert [t["id"] for t in ctx["open_tasks"]] == [rid]

    store.resolve_entry(rid, db_path=db)
    ctx = store.pull_context("proj", db_path=db)
    assert ctx["open_tasks"] == []


def test_invalid_type_rejected(db):
    with pytest.raises(ValueError):
        store.log_entry("proj", "not-a-type", "x", db_path=db)


def test_segment_scoping(db):
    store.log_entry("proj/backend", "decision", "use RFC 8693", db_path=db)
    # a parent pull includes descendants
    parent = store.pull_context("proj", db_path=db)
    assert any("RFC 8693" in d["content"] for d in parent["recent_decisions"])
    # an unrelated sibling does not
    other = store.pull_context("proj/frontend", db_path=db)
    assert other["recent_decisions"] == []


def test_search(db):
    store.log_entry("proj", "note", "token exchange details", db_path=db)
    hits = store.search_entries("token", db_path=db)
    assert any("token exchange" in h["content"] for h in hits)


def test_roadmap_rollup_counts_task_and_epic(db):
    epic = store.roadmap_add("epic", "auth epic", segment="proj", db_path=db)
    t1 = store.roadmap_add("task", "t1", parent_id=epic, segment="proj", db_path=db)
    store.roadmap_add("task", "t2", parent_id=epic, segment="proj", db_path=db)
    store.roadmap_set(t1, status="resolved", db_path=db)

    tree = store.roadmap_tree("proj", db_path=db)
    node = next(n for n in tree if n["id"] == epic)
    assert (node["done"], node["total"]) == (1, 2)

    # a shipped chunk logged as a resolved *leaf* epic also rolls up
    shipped = store.roadmap_add("epic", "shipped", segment="proj", db_path=db)
    store.roadmap_set(shipped, status="resolved", db_path=db)
    node = next(n for n in store.roadmap_tree("proj", db_path=db) if n["id"] == shipped)
    assert (node["done"], node["total"]) == (1, 1)


def test_roadmap_block_edge(db):
    a = store.roadmap_add("task", "blocker", segment="proj", db_path=db)
    b = store.roadmap_add("task", "blocked", segment="proj", db_path=db)
    store.roadmap_link(b, a, db_path=db)
    node = next(n for n in store.roadmap_tree("proj", db_path=db) if n["id"] == b)
    assert node["blocked_by"] == [a]


def test_handoff_routes_by_target(db):
    rid = store.handoff_entry("proj", "please implement X", "cursor", db_path=db)
    assert [i["id"] for i in store.pull_inbox("cursor", db_path=db)] == [rid]
    assert store.pull_inbox("claude-code", db_path=db) == []
    # handoff notes don't clutter recent_notes
    assert store.pull_context("proj", db_path=db)["recent_notes"] == []


def test_handoff_rejects_unknown_target(db):
    with pytest.raises(ValueError):
        store.handoff_entry("proj", "x", "nobody", db_path=db)


def test_completion_bare_claim(db):
    store.log_entry("proj", "change", "fixed the bug", db_path=db)  # no git ref
    rep = store.completion_report("proj", db_path=db)
    assert rep["bare_claims"] == 1
    assert rep["changes"][0]["verified"].startswith("CLAIM")


def test_constraints_include_ancestors(db):
    store.log_entry("proj", "decision", "top-level rule", db_path=db)
    got = store.decisions_for("proj/backend/auth", db_path=db)
    assert any("top-level rule" in d["content"] for d in got["decisions"])
