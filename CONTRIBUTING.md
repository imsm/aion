# Contributing to aionai

Thanks for your interest! aionai aims to stay small, sharp, and neutral — a broker of
working state, not a kitchen sink. Contributions that keep it that way are very welcome.

## Development setup

```bash
git clone https://github.com/imsm/aionai && cd aionai
python -m pip install -e ".[dev]"
pytest          # run the tests
ruff check .    # lint
```

The storage layer (`src/aionai/store.py`) is plain Python with no MCP dependency, so
most logic can be tested directly — please add a test for any behavior change.

## Guidelines

- **Keep the core neutral.** aionai brokers state; it does not become the source of
  truth (the repo, docs, and git stay authoritative).
- **Small, focused PRs.** One change per PR; describe the why.
- **Tests + lint must pass** (CI runs `pytest` and `ruff` on 3.10–3.13).
- **New MCP tools** should be thin wrappers in `aionai.server` over a tested function in
  `aionai.store`.
- Discuss larger changes in an issue first.

## Reporting bugs / ideas

Open a [GitHub issue](https://github.com/imsm/aionai/issues) with steps to reproduce or a
clear description of the proposed behavior.

By contributing, you agree that your contributions are licensed under the project's
[Apache-2.0](LICENSE) license.
