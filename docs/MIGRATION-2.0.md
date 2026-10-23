# Migrating to AbletonMCP 2.0

This document describes **2.0.0rc1**, an unpublished candidate. It does not certify
that automated checks or real Live acceptance have passed. Use the exact supplied
candidate revision or wheel; the current default Git branch and an older PyPI
release may contain different code. See the
[Windows candidate testing guide](WINDOWS-CANDIDATE-TESTING.md) for local acceptance.

## Upgrade both server and Remote Script

The MCP package and the script running inside Live must match. Install the
candidate package, run its `ableton-live-mcp install`, review any existing different
script before using `--replace`, then fully restart Live. Select AbletonMCP in an
unused Control Surface row with Input and Output set to None. Use the same custom
`--user-library` path for installation and `doctor --json` if your library moved.

The doctor report must confirm the installed script bytes, running bridge
`2.0.0rc1` and bridge protocol 2. Merely installing a new server cannot update a
script already loaded in Live. Offline `.als`/rack tools still work with Live closed.

Native platform wheels accelerate audio statistics. A universal Python wheel
provides the compiler-free fallback where there is no matching native wheel;
source builds select it with `ABLETON_MCP_PURE_PYTHON=1`. The fallback uses the same
public tools. The Live-side script remains Python 3.7 compatible; the server
requires Python 3.10 or newer.

## Read structured replies and truthful operation status

Tool replies retain MCP text and now consistently expose successful JSON under
`structuredContent.data`. A client that previously parsed JSON from text can
continue doing that, but custom integrations should prefer structured data.
For direct Python calls, tools that previously returned JSON-encoded objects
(such as `get_session_info`) now return dictionaries; remove a second
`json.loads()` around those changed returns. Intentionally textual tools, such as
`duplicate_to_arrangement`, retain string returns.

For example, read tempo from `result.structuredContent["data"]["tempo"]` after
checking `result.isError`. Failures set `isError=true` and expose
`structuredContent.error`, including partial-step details and an operation ID
when available. A failed multi-step operation can already have changed Live.

Edits run in serialized worker workflows. Cancelling a request prevents edits
that have not started; a started edit normally finishes its workflow. Recording
has explicit cancellation and recovery points. Do not assume cancellation undoes
an edit or stops audio immediately.

A timeout can mean an operation is queued or already running. Inspect its receipt
with `get_session_info(operation_id=...)` and verify current Live state before
resubmitting. The bridge retains the latest 256 mutation receipts; read polling
does not evict them. Reusing an operation ID while its receipt is retained does
not repeat that edit. A missing/expired receipt is not evidence of failure.

Transport replies include `requested`, `observed` and `confirmed` after a later
Live tick. Start dependent operations only after `confirmed=true`. Batch preflight
checks complete payloads before editing, but runtime failures stop with partial
results and do not automatically roll back completed edits. Some operations must
remain standalone; use tool errors and documentation rather than forcing them
into a batch.

## Clip automation: #4 and #5

The candidate adapts [#4](https://github.com/wstierhout/ableton-live-mcp/pull/4)
and [#5](https://github.com/wstierhout/ableton-live-mcp/pull/5) to the 2.0 contracts.
It registers 157 tools; `read_automation` is the additional tool.

`write_automation` still replaces the entire envelope for one device parameter on
one Session clip. Every point must have a finite beat time and native parameter
value. With no `duration`, ordered unique points hold until the next point or clip
end, replacing the old zero-length-step behavior. An explicit duration must be
positive and must not overlap the following segment. Ramp input becomes a stepped
envelope with a maximum of 4,096 written steps. A linear ramp is approximated by
stairs. Quantized parameters require integer point values and reject ramp mode
before clearing an envelope. Invalid payloads must preserve the old envelope;
verify that property during real Live acceptance.

In either mode, a first effective step at beat zero seeds the manual parameter
value, waits on later Live ticks, and then writes the envelope once. This changes
the manual value even when the clip is not playing. To avoid overriding existing
automation, a beat-zero write refuses a nonzero `automation_state` before making
any edit. Inspect the returned prior value, automation state and `start_settled`;
a warning is not confirmed playback. Beat-zero writes are rejected in batch
preflight and require a standalone call. Check Undo on real Live as part of
acceptance, including the manual value and the previous envelope.

`read_automation` samples the parameter envelope over a requested start/end range
and step (including the end when it lies on the grid), with at most 1,024 samples.
Explicit `times` are also supported. It returns sampled values from Live, not the
original point definitions. Live's exact
`value_at_time(t)` result is preserved, including its behavior at a step boundary;
when verifying a transition, sample just after the boundary as well as before it.
Sampling a ramp more coarsely than its generated steps can miss intermediate values.

[#8](https://github.com/wstierhout/ableton-live-mcp/pull/8) is explicitly deferred.
Its Arrangement automation tools rebuild clips and require a separate review of
note/envelope preservation. This candidate's automation tools operate on Session
clip envelopes, not Arrangement envelopes.

## Parameter conversion: overlapping #6 and #7

The candidate combines the shared conversion coverage from
[#6](https://github.com/wstierhout/ableton-live-mcp/pull/6) and
[#7](https://github.com/wstierhout/ableton-live-mcp/pull/7). For continuous
parameters, native numbers and numeric strings such as `0.5` and `"0.5"` mean the
same native value. For quantized parameters, strings match exact display labels
first, including numeric labels. If an enum displays `"8"` at native index 7,
`"8"` selects index 7 but the actual number `8` selects native index 8. The result
warns when label matching changes the numeric interpretation. Send an actual
number to choose by native position; ambiguous/uninspected labels are rejected.

A unit-bearing string such as `"-12 dB"` requests a supported display mapping.
Top-level and rack-chain setters use the same conversion rules, including Return
and Master device targets.

Read the parameter range and display before writing. Unsupported display mappings
fail instead of inventing a curve, and a successful response includes readback
and any warnings. Do not interpret a normalized mixer fader as a dB value: mixer
volume uses Live's normalized range, where 0.85 is unity.

## Prerelease version spelling

Python metadata, installed package/bridge versions and all PyPI launch pins use
`2.0.0rc1` (PEP 440). Desktop/plugin display manifests and the MCP Registry server
identity use `2.0.0-rc.1` (SemVer). The Registry's PyPI package version remains
`2.0.0rc1`. These identify the same candidate; release checks normalize only those
supported release/prerelease forms and still require exact Python package pins.
A final 2.0.0 release must update both sides together and ship its matching script.

## Roll back together

If acceptance fails, stop the candidate MCP server, reinstall the previous package
and its Remote Script, and restart Live. Keep the installer backup and your
pre-test disposable set. Reverting only the client package leaves a mismatched
script in Live; `doctor --json` should confirm the chosen package/script pair.
