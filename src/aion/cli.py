"""aion.cli — the console entry point (installed as the ``aion`` command).

    aion              run as an MCP server over stdio (what MCP clients launch)
    aion init         create the database
    aion log-commit   log the latest git commit (used by the post-commit hook)
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "serve"

    if cmd == "init":
        from . import store
        store.init_db()
        print(f"aion: initialised {store.DB_PATH}")
    elif cmd == "log-commit":
        from . import store
        store.log_commit()
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:  # serve (default) — MCP server over stdio
        from . import server, store
        store.init_db()
        server.mcp.run()


if __name__ == "__main__":
    main()
