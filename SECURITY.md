# Security policy

MIZAN is a research and portfolio project. It runs locally on generated data and is not a hardened production service. This page states what the code is designed to protect, what it does not protect, and how to report a problem.

## Supported versions

| Version | Supported |
|---|---|
| 3.0.x | yes |
| earlier | no |

## Trust boundaries

What the design is meant to guarantee, each with tests in `tests/`:

- **Signed results.** Each published result is bound by an Ed25519 signature to its job, input digest, profile, deployment revision, epoch and generation. `python -m mizan verify` checks a result offline; a changed number is refused.
- **Publisher identity.** The embedded key proves internal consistency only. Publisher identity requires comparing the key fingerprint through a trusted channel (`--expected-key`). The bundled exhibit's fingerprint is `9a627609771982875b75b6b28ff59091f40c5a116df11c1fffb94f37a51df436`.
- **Fenced publication.** At most one attempt of a job publishes; stale workers, cancelled jobs and old generations are refused.
- **Agents have no authority.** The MCP server exposes no tools to approve documents, assign authority, change thresholds or change deployments. Instructions inside agent-supplied documents are evidence, never authority, and produce REVIEW.
- **Operator token.** Private HTTP writes need the token in `var/mizan/operator.token`, created with mode 600.

What it does **not** protect against (documented limits):

- Workers run trusted code as the same OS user. Malicious workers need OS identity separation, restricted mounts, egress policy and protected keys.
- The MCP server runs as the operator's OS user; isolation beyond its tool surface is a deployment property.
- A witness on the same host is a protocol simulation, not independent attestation.
- PDF parsing is not sandboxed. Do not feed untrusted customer documents without an isolated parser.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting instead: on the repository page, open **Security**, then **Report a vulnerability**.

Include the affected version or commit, the steps to reproduce, and what an attacker gains. Examples in scope: a forged or altered result that `verify` accepts, an agent obtaining authority through MCP, an injected document line producing MEETS, a publication that bypasses fencing, or exposure of the operator token or signing key.

This is a personal project maintained on a best-effort basis. Reports will be acknowledged as soon as possible, and fixes will credit the reporter unless they prefer otherwise.
