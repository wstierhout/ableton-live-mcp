"""Browser behavior through the real bridge dispatcher and MCP result boundary."""

import asyncio
from types import SimpleNamespace as S

import pytest

from ableton_live_mcp.app import mcp
from ableton_live_mcp.tools import browser


def item(name, children=(), uri=None):
    return S(
        name=name,
        children=children,
        uri=uri,
        is_folder=bool(children),
        is_loadable=bool(uri),
        is_device=False,
    )


@pytest.fixture
def library(bridge, bridge_connection, monkeypatch):
    kit = item("Kit", uri="kit:one")
    roots = S(
        drums=item("Drums", [kit]), user_library=item("User Library"), load_item=lambda _: None
    )
    bridge.application = lambda: S(browser=roots)
    bridge._song.tracks = [S(name="Drums")]
    bridge._song.view = S(selected_track=None)
    monkeypatch.setattr(browser, "get_ableton_connection", lambda: bridge_connection)
    return roots


def call(name, arguments):
    return asyncio.run(mcp.call_tool(name, arguments))


def test_browser_listing_reports_real_roots_and_children(library):
    result = call("get_browser_tree", {}).structuredContent["data"]
    assert result["category_count"] == 2
    assert set(result["available_categories"]) == {"drums", "user_library"}
    assert {entry["path"] for entry in result["categories"]} == {"drums", "user_library"}
    children = call("get_browser_items_at_path", {"path": "drums"}).structuredContent["data"]
    assert children["item_count"] == 1 and not children["partial"]
    assert children["items"][0]["uri"] == "kit:one"
    assert (
        call("get_browser_items_at_path", {"path": "drums/Kit"}).structuredContent["data"]["items"]
        == []
    )


def test_missing_browser_path_stops_batch_before_later_edit(library, bridge):
    result = bridge.dispatch(
        {
            "type": "batch",
            "params": {
                "commands": [
                    {"type": "get_browser_items_at_path", "params": {"path": "drums/Missing"}},
                    {"type": "set_tempo", "params": {"tempo": 150}},
                ]
            },
        }
    )
    assert result["status"] == "error" and result["details"]["completed_count"] == 0
    assert bridge._song.tempo == 120


@pytest.mark.parametrize("path", ["drums/Missing", "user_library"])
def test_kit_preflight_leaves_devices_untouched(library, bridge, path):
    loaded = []
    bridge._load_browser_item = lambda track, uri: loaded.append(uri)
    result = call("load_drum_kit", {"track_index": 0, "rack_uri": "rack:one", "kit_path": path})
    assert result.isError and result.structuredContent["error"]
    assert loaded == []


def test_kit_load_failure_discloses_completed_rack(library, bridge):
    loaded = []

    def load(track, uri):
        if uri == "kit:one":
            raise RuntimeError("Kit became unavailable")
        loaded.append(uri)
        return {"loaded": True, "uri": uri}

    bridge._load_browser_item = load
    result = call("load_drum_kit", {"track_index": 0, "rack_uri": "rack:one", "kit_path": "drums"})
    error = result.structuredContent["error"]
    assert result.isError and loaded == ["rack:one"]
    assert error["completed_count"] == 1 and error["may_have_applied"]
    assert error["completed_results"][0]["uri"] == "rack:one"


def test_legacy_lookup_error_remains_an_mcp_failure(monkeypatch):
    sent = []
    connection = S(send_command=lambda name, params: sent.append(name) or {"error": "Missing"})
    monkeypatch.setattr(browser, "get_ableton_connection", lambda: connection)
    result = call("load_drum_kit", {"track_index": 0, "rack_uri": "rack:one", "kit_path": "drums"})
    assert result.isError and sent == ["get_browser_items_at_path"]


def test_search_handles_avoid_cold_scan_and_stale_handles_are_discarded(library, bridge):
    loaded = []
    library.load_item = loaded.append
    bridge._BROWSER_LOOKUP_NODES = 1
    failed = bridge.dispatch(
        {"type": "load_browser_item", "params": {"track_index": 0, "item_uri": "kit:one"}}
    )
    assert failed["status"] == "error" and failed["details"]["partial"]
    assert failed["details"]["limit_reasons"] == ["scan_budget"] and loaded == []
    found = bridge.dispatch({"type": "search_browser", "params": {"query": "Kit"}})
    assert found["result"]["matches"][0]["uri"] == "kit:one"
    bridge.dispatch(
        {"type": "load_browser_item", "params": {"track_index": 0, "item_uri": "kit:one"}}
    )
    assert loaded == [library.drums.children[0]]
    loaded[0].uri = "invalidated"
    library.drums.children = [item("Replacement", uri="kit:one")]
    bridge._BROWSER_LOOKUP_NODES = 10
    bridge.dispatch(
        {"type": "load_browser_item", "params": {"track_index": 0, "item_uri": "kit:one"}}
    )
    assert loaded[-1].name == "Replacement"


def test_bounded_listing_does_not_claim_complete(library, bridge):
    bridge._BROWSER_LIST_LIMIT = 1
    library.drums.children.append(item("Second kit", uri="kit:two"))
    result = call("get_browser_items_at_path", {"path": "drums"}).structuredContent["data"]
    assert result["partial"] and result["item_count"] == 1
    assert result["limit_reasons"] == ["listing_budget"]


def test_inaccessible_root_is_partial_and_methods_are_not_categories(
    bridge, monkeypatch, bridge_connection
):
    class UnreadableBrowser:
        user_library = item("User Library")

        @property
        def drums(self):
            raise RuntimeError("Library is rebuilding")

    bridge.application = lambda: S(browser=UnreadableBrowser())
    monkeypatch.setattr(browser, "get_ableton_connection", lambda: bridge_connection)
    result = call("get_browser_tree", {}).structuredContent["data"]
    assert result["partial"] and result["errors"]["drums"] == "Library is rebuilding"
    assert result["category_count"] == 1
    assert call("get_browser_items_at_path", {"path": "load_item"}).isError
