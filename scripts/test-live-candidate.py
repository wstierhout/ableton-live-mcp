"""Run explicit acceptance cases against a disposable set in actual Live.

This imports safely; only the command-line entry point starts an MCP server and
runs the selected case. See docs/WINDOWS-CANDIDATE-TESTING.md before use.
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

TARGET = {"track_index": 0, "clip_index": 0, "device_index": 1}


def select_gain(payload, location):
    """Use only an unambiguous enabled continuous parameter returned by Live."""
    matches = [
        p
        for p in payload["parameters"]
        if p["name"] in {"Gain", "Output"} and p["is_enabled"] and not p["is_quantized"]
    ]
    assert len(matches) == 1, (
        f"{location}: expected exactly one enabled continuous Gain or Output; "
        f"found {len(matches)}. Inspect the stock Utility fixture before editing."
    )
    selected = matches[0]
    print(
        json.dumps(
            {
                "selected_parameter": {
                    "location": location,
                    "device": payload["device"],
                    "name": selected["name"],
                    "index": selected["index"],
                    "min": selected["min"],
                    "max": selected["max"],
                    "automation_state": selected.get("automation_state"),
                    "source": "matching parameter getter response",
                }
            }
        ),
        flush=True,
    )
    return selected


async def exercise(session, case):
    async def call(name, args=None, error=False):
        reply = await session.call_tool(name, args or {})
        print(
            json.dumps(
                {"tool": name, "arguments": args or {}, "reply": reply.model_dump(mode="json")}
            ),
            flush=True,
        )
        assert bool(reply.isError) == error, f"Unexpected result from {name}"
        payload = reply.structuredContent
        assert isinstance(payload, dict) and "data" in payload, "Missing structured result"
        if error:
            assert payload.get("error"), "Missing structured failure"
            return payload["error"]
        assert payload.get("error") is None
        return payload["data"]

    state = await call("get_session_info")
    assert state["server_version"] == state["bridge_version"] == "2.0.0rc1"
    assert state["bridge_protocol"] >= 2
    assert not state["is_playing"] and not state["record_mode"]
    snapshot = await call("get_session_snapshot")
    assert not any(t.get("armed") for t in snapshot["tracks"])
    track = await call("get_track_info", {"track_index": 0})
    assert track["name"] == "MCP20 TEST MIDI", "Wrong disposable fixture"
    top = await call("get_device_parameters", {"track_index": 0, "device_index": 1})
    gain = select_gain(top, "regular")
    target = {**TARGET, "parameter": gain["name"]}
    if case != "inventory":
        assert gain.get("automation_state") == 0, (
            "BLOCKED: fixture parameter has active/overridden or unknown automation state; "
            "stop the case and inspect Live before any write"
        )

    async def require_inactive_automation():
        current = await call("get_device_parameters", {"track_index": 0, "device_index": 1})
        parameter = select_gain(current, "regular-before-automation-write")
        assert parameter["name"] == gain["name"] and parameter["index"] == gain["index"]
        assert parameter.get("automation_state") == 0, (
            "BLOCKED: Live reports active/overridden or unknown automation; "
            "do not bypass the guard or blindly repeat this envelope write"
        )

    lo, hi = gain["min"], gain["max"]
    low, high = lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.75

    if case == "inventory":
        tools = (await session.list_tools()).tools
        print(json.dumps({"tool_count": len(tools), "names": sorted(t.name for t in tools)}))
        assert len(tools) == 157
        returns = await call("get_return_device_parameters", {"return_index": 0, "device_index": 0})
        master = await call("get_master_device_parameters", {"device_index": 0})
        select_gain(returns, "return")
        select_gain(master, "master")
        await call("get_rack_chains", {"track_index": 0, "device_index": 2})
        return

    if case == "parameters":
        targets = [
            ("set_device_parameter", {"track_index": 0, "device_index": 1}),
            ("set_return_device_parameter", {"return_index": 0, "device_index": 0}),
            ("set_master_device_parameter", {"device_index": 0}),
            (
                "set_chain_device_parameter",
                {"track_index": 0, "device_index": 2, "chain_index": 0, "chain_device_index": 0},
            ),
        ]
        db_results = []
        readers = {
            "set_device_parameter": (
                "get_device_parameters",
                {"track_index": 0, "device_index": 1},
            ),
            "set_return_device_parameter": (
                "get_return_device_parameters",
                {"return_index": 0, "device_index": 0},
            ),
            "set_master_device_parameter": ("get_master_device_parameters", {"device_index": 0}),
        }
        # Resolve every readable target before the first parameter mutation.
        selected = {"set_device_parameter": gain}
        for name in ("set_return_device_parameter", "set_master_device_parameter"):
            reader, read_args = readers[name]
            values = await call(reader, read_args)
            selected[name] = select_gain(values, name)
            assert selected[name].get("automation_state") == 0, (
                "BLOCKED: return/Master fixture has active/overridden or unknown automation"
            )
            assert math.isclose(selected[name]["min"], lo, abs_tol=1e-8)
            assert math.isclose(selected[name]["max"], hi, abs_tol=1e-8), (
                "Utilities must have matching native ranges"
            )
        await call("get_rack_chains", {"track_index": 0, "device_index": 2})
        # No chain-parameter getter exists. Require the same unmodified stock
        # Utility and human confirmation before using the index returned for
        # its top-level copy. UI labels can differ from Live API parameter names.
        print(
            "Inspect track 0 / rack device 2 / chain 0 / device 0. Confirm an "
            "identical, unmodified stock Utility with no active/overridden "
            "automation. Type SAME UTILITY to use the matching parameter index:",
            flush=True,
        )
        confirmed = input()
        assert confirmed == "SAME UTILITY", "Nested Utility mismatch; no parameters were changed"
        selected["set_chain_device_parameter"] = gain
        print(
            json.dumps(
                {
                    "selected_parameter": {
                        "location": "chain",
                        "name_from_identical_top_level_device": gain["name"],
                        "parameter_index": gain["index"],
                        "source": (
                            "returned top-level index plus explicit "
                            "identical-stock-Utility confirmation"
                        ),
                    }
                }
            ),
            flush=True,
        )
        for name, address in targets:
            parameter = selected[name]
            reference = (
                parameter["index"] if name == "set_chain_device_parameter" else parameter["name"]
            )
            args = {**address, "parameter": reference}
            number = await call(name, {**args, "value": low})
            string = await call(name, {**args, "value": str(low)})
            assert math.isclose(number["native"], string["native"], abs_tol=1e-5)
            db = await call(name, {**args, "value": "-12 dB"})
            assert "dB" in db["display"]
            observed = float(db["display"].replace("\u2212", "-").replace("dB", "").strip())
            assert abs(observed + 12) <= 0.15, db
            db_results.append(db["native"])
            await call(name, {**args, "value": "not-a-parameter-value"}, error=True)
            if name in readers:
                reader, read_args = readers[name]
                values = await call(reader, read_args)
                unchanged = select_gain(values, name)
                assert (
                    unchanged["name"] == parameter["name"]
                    and unchanged["index"] == parameter["index"]
                )
                assert math.isclose(unchanged["value"], db["native"], abs_tol=1e-5)
            else:
                # There is no nested-device parameter getter in this toolset.
                print(
                    "Inspect identical rack-chain Utility gain control "
                    f"(parameter index {parameter['index']}): still -12 dB "
                    "after rejection? Type PASS:",
                    flush=True,
                )
                assert input().strip() == "PASS"
            await call(name, {**args, "value": "-inf dB"})
            await call(name, {**args, "value": "nan"}, error=True)
            await call(name, {**args, "value": "inf"}, error=True)
            await call(name, {**args, "value": "-12 dB"})
        assert max(db_results) - min(db_results) <= 1e-5
        return

    if case in {"automation", "automation-points-zero"}:
        # Each case needs its own pristine saved fixture, with no existing clip.
        assert not track["clip_slots"][0]["has_clip"], "Reopen the empty fixture first"
        await call("create_clip", {"track_index": 0, "clip_index": 0, "length": 4.0})
        baseline_notes = await call("get_clip_notes", {"track_index": 0, "clip_index": 0})
        sample_times = [0.001, 0.251, 1.501, 2.001, 3.999]
        empty = await call("read_automation", {**target, "times": sample_times})
        assert empty["has_envelope"] is False, "Fresh fixture unexpectedly has an envelope"
        await require_inactive_automation()
        if case == "automation-points-zero":
            request = {
                **target,
                "points": [{"time": 0.0, "value": low}, {"time": 2.0, "value": high}],
            }
            expected = [low, low, low, high, high]
        else:
            # Seed the beat-zero ramp before there is an active envelope.
            request = {
                **target,
                "mode": "ramp",
                "step_length": 0.25,
                "points": [{"time": 0.0, "value": low}, {"time": 3.0, "value": high}],
            }
            # Twelve stairs reach the final value on stair 11 (beat 2.75).
            expected = [
                low,
                low + (high - low) / 11,
                low + (high - low) * 6 / 11,
                low + (high - low) * 8 / 11,
                high,
            ]
        written = await call("write_automation", request)
        assert written["start_settled"] is True, (
            "Beat-zero start unconfirmed; retain warning and inspect Live"
        )
        assert written["prior_automation_state"] == 0
        assert math.isclose(written["prior_value"], gain["value"], abs_tol=1e-5)
        seeded = await call("read_automation", {**target, "times": sample_times})
        assert_samples(seeded, expected, hi - lo)

        # One Undo must recover both the prior manual value and missing envelope.
        await call("undo")
        undone = await call("read_automation", {**target, "times": sample_times})
        assert undone["has_envelope"] is False
        prior = await call("get_device_parameters", {"track_index": 0, "device_index": 1})
        prior_parameter = select_gain(prior, "regular-after-seed-undo")
        assert math.isclose(prior_parameter["value"], gain["value"], abs_tol=1e-5)
        await call("redo")
        restored = await call("read_automation", {**target, "times": sample_times})
        assert_samples(restored, expected, hi - lo)
        assert await call("get_clip_notes", {"track_index": 0, "clip_index": 0}) == baseline_notes
        if case == "automation-points-zero":
            return

        # A later-starting replacement does not seed and is permitted even when
        # the preceding envelope makes automation_state nonzero.
        current = await call("get_device_parameters", {"track_index": 0, "device_index": 1})
        select_gain(current, "regular-before-nonzero-points-replacement")
        replacement = await call(
            "write_automation",
            {
                **target,
                "points": [
                    {"time": 0.25, "duration": 1.75, "value": low},
                    {"time": 2.0, "duration": 2.0, "value": high},
                ],
            },
        )
        assert replacement["start_value_set"] is None
        point_times = [0.251, 1.999, 2.001, 3.999]
        before_invalid = await call("read_automation", {**target, "times": point_times})
        assert_samples(before_invalid, [low, low, high, high], hi - lo)
        # Invalid explicit duration must not clear the replacement envelope.
        await call(
            "write_automation",
            {**target, "points": [{"time": 0.25, "duration": 0.0, "value": low}]},
            error=True,
        )
        after_invalid = await call("read_automation", {**target, "times": point_times})
        assert after_invalid == before_invalid, "Invalid request changed the existing envelope"
        assert await call("get_clip_notes", {"track_index": 0, "clip_index": 0}) == baseline_notes
        # Undo the valid replacement, not the rejected request. Recover the ramp.
        await call("undo")
        ramp_restored = await call("read_automation", {**target, "times": sample_times})
        assert_samples(ramp_restored, expected, hi - lo)
        await call("redo")
        points_restored = await call("read_automation", {**target, "times": point_times})
        assert_samples(points_restored, [low, low, high, high], hi - lo)
        await call("read_automation", {**target, "start": 0.0, "end": 4.0, "step": 1.0})
        await call("read_automation", {**target, "times": [0.0], "step": 1.0}, error=True)
        return

    if case == "batch":
        original = track["name"]
        await call(
            "batch_commands",
            {
                "commands": [
                    {
                        "type": "set_track_name",
                        "params": {"track_index": 0, "name": "MUST NOT APPLY"},
                    },
                    {"type": "no_such_command", "params": {}},
                ]
            },
            error=True,
        )
        assert (await call("get_track_info", {"track_index": 0}))["name"] == original
        operation = uuid.uuid4().hex
        commands = [
            {"type": "set_track_name", "params": {"track_index": 0, "name": "BATCH TEST"}},
            {"type": "set_track_pan", "params": {"track_index": 0, "pan": 0.25}},
        ]
        first = await call("batch_commands", {"commands": commands, "operation_id": operation})
        again = await call("batch_commands", {"commands": commands, "operation_id": operation})
        assert again == first
        await call("undo")
        restored = await call("get_track_info", {"track_index": 0})
        assert restored["name"] == original and abs(restored["panning"] - track["panning"]) < 1e-5
        error = await call(
            "batch_commands",
            {
                "commands": [
                    {
                        "type": "set_track_name",
                        "params": {"track_index": 0, "name": "PARTIAL TEST"},
                    },
                    {
                        "type": "set_device_parameter",
                        "params": {
                            "track_index": 0,
                            "device_index": 1,
                            "parameter": gain["name"],
                            "value": "not-a-parameter-value",
                        },
                    },
                ]
            },
            error=True,
        )
        assert error["phase"] == "execution" and error["completed_count"] == 1
        assert (await call("get_track_info", {"track_index": 0}))["name"] == "PARTIAL TEST"
        await call("undo")
        assert (await call("get_track_info", {"track_index": 0}))["name"] == original
        return

    if case == "record":
        # First prepare audible arrangement material at beats 0-4 in the UI.
        result = await call("record_section", {"start_beat": 0.0, "end_beat": 4.0, "cleanup": True})
        assert result["transport_recovery"]["confirmed_stopped"]
        assert not result["cleanup_errors"] and result["cleaned_up"]
        assert result["file_path"] and Path(result["file_path"]).is_file()
        print(json.dumps({"audition_required": result["file_path"]}))
        return

    if case == "cancel":
        assert not state["loop"], "Disable Arrangement loop first"
        original_ids = {t["id"] for t in snapshot["tracks"]}
        # MCP SDK 1.x exposes its request sequence internally. Capture the next ID
        # immediately before this one request; cancelling only the Python Task
        # does NOT send an MCP notifications/cancelled message in this SDK.
        request_id = session._request_id
        pending = asyncio.create_task(
            session.call_tool(
                "record_section", {"start_beat": 0.0, "end_beat": 64.0, "cleanup": True}
            )
        )
        await asyncio.sleep(0)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = await call("get_session_info")
            if current["is_playing"] and current["record_mode"]:
                break
            assert not pending.done(), "Recording ended or failed before cancellation"
            await asyncio.sleep(0.2)
        else:
            raise AssertionError("Recording never reached the active state; inspect Live")
        await session.send_notification(
            types.ClientNotification(
                types.CancelledNotification(
                    params=types.CancelledNotificationParams(
                        requestId=request_id, reason="Windows acceptance"
                    )
                )
            )
        )
        try:
            reply = await asyncio.wait_for(pending, 30)
            print(json.dumps({"cancel_reply": reply.model_dump(mode="json")}))
            assert reply.isError, (
                "Recording completed successfully; cancellation was not demonstrated"
            )
            assert "cancel" in json.dumps(reply.model_dump(mode="json")).casefold(), (
                "Recording failed for a reason other than cancellation"
            )
        except McpError as exc:
            print(json.dumps({"cancel_rpc": exc.error.model_dump(mode="json")}))
            assert exc.error.code == 0 and "cancel" in exc.error.message.casefold(), (
                "Unexpected RPC error; cancellation was not demonstrated"
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = await call("get_session_info")
            settled = await call("get_session_snapshot")
            if (
                not current["is_playing"]
                and not current["record_mode"]
                and {t["id"] for t in settled["tracks"]} == original_ids
                and not any(t.get("armed") for t in settled["tracks"])
            ):
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError(
                "Cancellation recovery did not settle; stop Live manually and retain evidence"
            )
        # A following edit demonstrates that cancellation released the edit queue.
        await call("set_track_name", {"track_index": 0, "name": "MCP20 TEST MIDI"})
        return


# read_automation returns sampled values rather than editable breakpoint events.
def assert_samples(result, expected, span):
    actual = [point["value"] for point in result["samples"]]
    assert len(actual) == len(expected)
    assert all(abs(a - b) <= max(abs(span) * 0.001, 1e-5) for a, b in zip(actual, expected)), (
        actual,
        expected,
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "case",
        choices=[
            "inventory",
            "parameters",
            "automation",
            "automation-points-zero",
            "batch",
            "record",
            "cancel",
        ],
    )
    args = parser.parse_args()
    env = {**os.environ, "ABLETON_HOST": "127.0.0.1", "ABLETON_PORT": "9877"}
    env.pop("ABLETON_TOOLSETS", None)
    params = StdioServerParameters(
        command=sys.executable, args=["-I", "-m", "ableton_live_mcp.server"], env=env
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(
            reader, writer, read_timeout_seconds=timedelta(seconds=60)
        ) as session:
            await session.initialize()
            await exercise(session, args.case)
    print(json.dumps({"case": args.case, "status": "passed"}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
