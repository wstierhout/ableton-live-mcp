"""Reproducible local benchmarks. Creates only temporary synthetic audio/ALS files."""

import argparse
import gzip
import json
import math
import platform
import statistics
import tempfile
import time
import tracemalloc
import wave
from pathlib import Path

from ableton_live_mcp import audio_stats
from ableton_live_mcp.tools import _als_parse, audio_analysis


def median_ms(callback, repeats):
    times = []
    for _ in range(repeats):
        started = time.perf_counter()
        callback()
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times)


def benchmark(repeats=3):
    if not audio_stats.NATIVE_AUDIO:
        raise RuntimeError("Install/build the native wheel before benchmarking")
    report = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "repeats": repeats,
        "timing": "median wall time, warm local files",
        "results": [],
    }
    with tempfile.TemporaryDirectory(prefix="ableton-performance-") as directory:
        root = Path(directory)
        block = b"".join(
            int(math.sin(i * 0.023) * 4000000).to_bytes(3, "little", signed=True) * 2
            for i in range(4800)
        )
        for seconds in (30, 120):
            path = root / f"pcm-{seconds}.wav"
            with wave.open(str(path), "wb") as output:
                output.setparams((2, 3, 48000, 0, "NONE", "not compressed"))
                for _ in range(seconds * 10):
                    output.writeframesraw(block)
            audio_analysis.pcm_accumulate = audio_stats.python_pcm_accumulate
            reference = audio_analysis.measure_audio(path, seconds)
            python_ms = median_ms(lambda: audio_analysis.measure_audio(path, seconds), repeats)
            audio_analysis.pcm_accumulate = audio_stats.pcm_accumulate
            assert audio_analysis.measure_audio(path, seconds) == reference
            native_ms = median_ms(lambda: audio_analysis.measure_audio(path, seconds), repeats)
            report["results"].append(
                {
                    "case": f"complete {seconds}s stereo 48kHz 24-bit WAV analysis",
                    "python_ms": python_ms,
                    "native_ms": native_ms,
                    "speedup": python_ms / native_ms,
                    "exact_result_match": True,
                }
            )
        # Separate the in-process kernel comparison from a full worker launch.
        # Startup/import overhead must be measured before claiming a tool speedup.
        import av

        from ableton_live_mcp import _audio_decode

        with (
            av.open(str(root / "pcm-30.wav")) as source,
            av.open(str(root / "decoded.flac"), "w") as output,
        ):
            stream = output.add_stream("flac", rate=48000)
            stream.layout = "stereo"
            for frame in source.decode(audio=0):
                for packet in stream.encode(frame):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        path = root / "decoded.flac"
        _audio_decode.float_stats = audio_stats.python_float_stats
        reference = _audio_decode.decode_and_measure(path, 30)
        python_ms = median_ms(lambda: _audio_decode.decode_and_measure(path, 30), repeats)
        _audio_decode.float_stats = audio_stats.float_stats
        actual = _audio_decode.decode_and_measure(path, 30)
        assert actual == reference
        native_ms = median_ms(lambda: _audio_decode.decode_and_measure(path, 30), repeats)
        report["results"].append(
            {
                "case": "30s FLAC decode/resample/measure (excluding worker startup)",
                "python_ms": python_ms,
                "native_ms": native_ms,
                "speedup": python_ms / native_ms,
                "exact_result_match_for_fixture": True,
            }
        )
        assert audio_analysis.measure_audio(path, 30) == reference
        report["results"].append(
            {
                "case": "complete 30s FLAC analysis including worker startup",
                "native_ms": median_ms(lambda: audio_analysis.measure_audio(path, 30), repeats),
                "exact_result_match_for_fixture": True,
            }
        )
        events = "".join(
            f'<MidiNoteEvent Pitch="60" Time="{i}" Duration="1" Velocity="100"/>'
            for i in range(100000)
        )
        xml = (
            '<Ableton><LiveSet><Tracks><MidiTrack><Name><EffectiveName Value="Synthetic"/>'
            f"</Name><MidiClip><Notes>{events}</Notes></MidiClip>"
            "</MidiTrack></Tracks></LiveSet></Ableton>"
        )
        path = root / "large.als"
        path.write_bytes(gzip.compress(xml.encode()))
        full_ms = median_ms(
            lambda: _als_parse._load(path, include_notes=True, refresh=True), repeats
        )
        summary_ms = median_ms(
            lambda: _als_parse._load(path, include_notes=False, refresh=True), repeats
        )
        warm_ms = median_ms(lambda: _als_parse._load(path, include_notes=False), repeats)
        peak_bytes = {}
        for label, include_notes in (("full", True), ("summary", False)):
            tracemalloc.start()
            _als_parse._load(path, include_notes=include_notes, refresh=True)
            peak_bytes[label] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        report["results"].append(
            {
                "case": "100000-note synthetic ALS",
                "full_parse_ms": full_ms,
                "cold_summary_ms": summary_ms,
                "cached_summary_ms": warm_ms,
                "summary_speedup": full_ms / summary_ms,
                "peak_allocated_bytes": peak_bytes,
            }
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.repeats), indent=2))
