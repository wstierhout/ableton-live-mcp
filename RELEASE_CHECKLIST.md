# Release candidate: 1.8.1

This is a preparation checklist, not evidence that the release is published.

- [x] Automated tests: 101 passed, 1 factory-fixture test skipped, in a clean Python 3.13 environment.
- [x] Lint, formatting, package builds, metadata validation and real MCP stdio handshake.
- [x] Refreshed dependency lock/security floors; clean dependency audit reports no known vulnerabilities.
- [x] Codex manifest and workflow skill pass the official validators; ZIP builder included.
- [ ] Smoke-test in a disposable Live set on macOS and Windows; record the exact Live version.
- [ ] Confirm the new Remote Script is installed and Live has restarted.
- [ ] Review the independently maintained Codex plugin and test installation in a fresh Codex session.
- [x] Publish the Python package before deploying website commands pinned to 1.8.1.
- [x] Commit, push, tag and publish only after maintainer approval.

No account settings, marketplace installations, releases or deployments are changed by this file.

The original `.venv` was preserved. Use `uv sync --frozen --extra dev` explicitly
before returning to that environment; release validation used a separate clean
temporary environment. The source archive includes the plugin and metadata needed
by its tests; the wheel contains the runtime bridge, not an auto-installed plugin.

Before publication, test a temporary plugin copy against the locally built wheel
if the pinned package version is not yet available on PyPI. Do not publish a plugin
whose stdio arguments still contain a developer's absolute wheel path.
