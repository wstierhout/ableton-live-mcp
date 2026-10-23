"""Installed-worker isolation and imports; also run against the portable wheel."""

import json
import subprocess
import sys
import wave
from pathlib import Path

import ableton_live_mcp
from ableton_live_mcp.tools.audio_analysis import measure_audio


def test_worker_ignores_cwd_and_pythonpath_packages(tmp_path, monkeypatch):
    hostile = tmp_path / "ableton_live_mcp"
    hostile.mkdir()
    marker = tmp_path / "executed.txt"
    attack = f"from pathlib import Path\nPath({str(marker)!r}).write_text('unexpected import')\n"
    (hostile / "__init__.py").write_text(attack)
    (hostile / "_audio_decode.py").write_text(attack)
    (tmp_path / "sitecustomize.py").write_text(attack)
    (tmp_path / "av.py").write_text(attack)
    sample = tmp_path / "audio.wave"  # .wave deliberately takes the decoder-worker path
    with wave.open(str(sample), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x00" * 480)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    result = measure_audio(sample)
    assert result["evidence"] == "measured_decoded_audio"
    assert result["sample_rate"] == 48000
    assert not marker.exists()


def test_decoder_and_cli_do_not_import_server_stack():
    package_root = Path(ableton_live_mcp.__file__).resolve().parent.parent
    code = """
import sys, json
sys.path.insert(0, sys.argv[1])
import ableton_live_mcp._audio_decode
import ableton_live_mcp.cli
from ableton_live_mcp import AbletonConnection, get_ableton_connection
assert callable(AbletonConnection) and callable(get_ableton_connection)
heavy = ('mcp', 'anyio', 'pydantic')
print(json.dumps([name for name in sys.modules if name.split('.')[0] in heavy]))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(package_root)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == []
