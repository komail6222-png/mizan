# Contributing

Thank you for your interest in MIZAN.

## Setup

Linux or macOS (on Windows, WSL2 with the project on the Linux filesystem), Python 3.12 or 3.13:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt mcp && pip install -e .
python -m unittest discover -s tests -v
```

## Before opening a pull request

```bash
python -m unittest discover -s tests -v
python scripts/safety_suite.py --quick --out /tmp/safety.json
node scripts/check_exhibit_logic.mjs
python -m mizan verify reports/demo/demo_results.json --expected-key 9a627609771982875b75b6b28ff59091f40c5a116df11c1fffb94f37a51df436
```

## Ground rules

- **Fix with evidence.** Reproduce the problem first. Keep the change small and add a test that fails before it and passes after.
- **Do not touch signed data.** `reports/demo/demo_results.json` and the pages built from it (`reports/demo/MIZAN_Demo.html`, `mizan/web/index.html`, `spaces/*/index.html`) must keep verifying against the pinned key. Never re-sign them.
- **Numbers come from `reports/`.** A claim in documentation must point to the report file that supports it. All bundled data is generated. The one real dataset, VisA (`reports/visa/`), is fetched rather than bundled, and must be named and attributed (`docs/DATA_LICENSES.md`).
- **Keep the agent boundary.** New MCP tools must not grant authority, change thresholds or change deployments.
- Write code, commit messages and technical documentation in English. Arabic user-facing text is welcome alongside.

Security problems: see [`SECURITY.md`](SECURITY.md).
