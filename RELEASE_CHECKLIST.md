# Release candidate: 2.0.0rc1

This is a preparation checklist, not evidence that the release is published or
that checks have passed. Record results against the final candidate commit and
artifact hashes; results for an earlier candidate do not establish acceptance.

## Candidate and audit

- [ ] Integrate #4 and #5 with the 2.0 validation, worker and receipt contracts.
- [ ] Combine #6/#7 parameter conversion fixes; credit both contributions.
- [ ] Confirm #8 Arrangement automation tools are excluded and remain deferred.
- [ ] Complete the requested Opus 5.5 audit before running automated checks; record
      its exact model identity, findings and fixes. If that model is unavailable,
      disclose the limitation instead of labelling a different review as Opus 5.5.
- [ ] Review the migration notes and Windows instructions against the final tools.

## Automated validation (after audit fixes)

- [ ] Run `uv sync --frozen --extra dev` in a clean candidate environment.
- [ ] Run `uv run --frozen --extra dev ruff check .` and
      `uv run --frozen --extra dev ruff format --check .`.
- [ ] Run `uv run --frozen --extra dev pytest -q` and review any skips.
- [ ] Confirm the Remote Script parses with Python 3.7 grammar (Live 11).
- [ ] Run `uv run --frozen python scripts/export_tool_catalog.py --check` and
      export the full 157-tool catalog. Check both prerelease version spellings:
      Python/bridge/PyPI pins `2.0.0rc1`; SemVer manifests `2.0.0-rc.1`.
- [ ] Validate metadata against the official Desktop/Registry/plugin schemas.
- [ ] Build the sdist, native wheel and universal Python fallback wheel. Test
      installed distributions outside editable/source imports, including a real
      MCP stdio initialize/list/call flow and setup/doctor commands.
- [ ] Complete native/fallback wheel CI on supported macOS, Windows and Linux
      targets. Local macOS builds do not establish Windows wheel compatibility.
- [ ] Refresh the website's generated catalog/version pins from this exact
      candidate; keep deployment separate from local verification.

## Real Live acceptance

Follow [the Windows candidate testing guide](docs/WINDOWS-CANDIDATE-TESTING.md).
Use disposable sets on macOS and Windows and record the exact Live edition and
version. Test Live 11 and Live 12 where support is claimed.

- [ ] Install the candidate Remote Script and fully restart Live. `doctor --json`
      confirms installed bytes and running bridge match `2.0.0rc1`, protocol 2.
- [ ] Verify automation duration/holds/ramps and `read_automation` sample readback,
      malformed-write preservation, and Undo. Read just after step boundaries.
- [ ] Verify native numeric strings, dB displays and enum labels on top-level,
      Return, Master and rack-chain parameters, including unsupported mappings.
- [ ] Verify batches disclose partial edits, transport confirmation, operation
      receipts after uncertain replies, cancellation and recording recovery.
- [ ] Audition generated content and inspect a real recorded WAV; fixture tests
      and file existence alone do not establish musical or audio correctness.
- [ ] Test the independently maintained plugin in a fresh client session against
      the candidate wheel. Keep developer-specific wheel paths out of published
      metadata.

## Publication

- [ ] Decide whether to publish an RC or promote the accepted candidate to stable
      2.0.0. If the version changes, update all matching metadata and bridge pins,
      rebuild the final artifacts and verify those artifacts before release.
- [ ] Publish the Python package before deploying website/plugin commands pinned
      to it. A pin to an unpublished version is not an installation path.
- [ ] Publish the matching release/tag and Registry/plugin metadata, preserving
      contributor credits and the deferred #8 scope.

This file makes no account, installation, release or deployment changes. The
source archive contains migration documentation and release metadata; the wheel
contains the runtime bridge, not an auto-installed plugin.
