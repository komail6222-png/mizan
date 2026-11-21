# CLAUDE.md

Guidance for AI coding sessions on MIZAN. Read `README.md` for the overview and `docs/TECHNICAL_GUIDE.md` for operation.

## Environment

Linux or macOS only: the broker uses `fcntl` and `resource`. On Windows, work inside WSL2 on the Linux filesystem (for example `~/projects/mizan`), not under `/mnt/c`. Python 3.12 (3.13 also tested).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt mcp playwright && pip install -e .
mizan doctor                      # reports SQLite; follow the README section if it prints SQLITE_PATCH
```

## Commands

```bash
python -m unittest discover -s tests -v                       # 102 tests
python -m mizan run sanad examples/sanad.json --out /tmp/sanad.json
python -m mizan verify /tmp/sanad.json
python -m mizan explain /tmp/sanad.json --lang ar
python -m mizan verify reports/demo/demo_results.json --expected-key 9a627609771982875b75b6b28ff59091f40c5a116df11c1fffb94f37a51df436
python scripts/safety_suite.py --quick --out /tmp/safety.json  # default --out overwrites reports/safety/safety.json
node scripts/check_exhibit_logic.mjs
python scripts/check_exhibits.py --chromium PATH --out /tmp/browser.json
python scripts/build_site.py                                   # GitHub Pages site into _site/
```

## Layout

| Path | Contents |
|---|---|
| `mizan/core/` | broker and store (SQLite WAL, fencing), signed artifacts, audit witness, anchoring, recovery |
| `mizan/sanad/`, `mizan/basira/`, `mizan/rasid/` | the three decision engines |
| `mizan/agent.py` | MCP server for AI agents (no authority tools) |
| `mizan/explain.py`, `mizan/receipts.py` | proof-only explanations, offline receipt verification |
| `mizan/simulation.py`, `mizan/safety.py` | deterministic simulation and metamorphic safety suites |
| `scripts/` | evaluation, report, exhibit and site builders, browser checks |
| `reports/` | the evidence: every published number comes from here |
| `docs/` | release notes, qualification record, technical guide, design history |

## Invariants: never break these

- **Signed exhibit data is frozen.** Do not regenerate or re-sign `reports/demo/demo_results.json`, `reports/demo/MIZAN_Demo.html`, `mizan/web/index.html` or `spaces/*/index.html`. Never run `python -m mizan demo` with `--out reports/demo`: it creates a new key and breaks the pinned fingerprint `9a627609…df436`. Rebuild pages only with `scripts/build_exhibit.py` and `scripts/build_spaces.py` from the existing signed data.
- **No invented numbers.** Every figure in the README, docs or posts must be traceable to a file in `reports/`. All datasets are generated fixtures; never describe them as real.
- **Agent versus operator.** AI agents (MCP) may submit, explain and verify. Approving documents, assigning authority, changing thresholds, promotion, rollback, upgrade, revocation, backup, restore and anchoring stay with the human operator. Do not add MCP tools for them.
- **Minimal fixes.** Change code only to fix a reproduced failure, with the smallest change and a test that proves it.
- **Never commit** `var/`, `.venv/`, `operator.token`, `*.key`, witness directories or `launch-kit/`. Regenerate `SHA256SUMS` after changing tracked files.
