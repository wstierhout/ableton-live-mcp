import hashlib
import math
import struct
import subprocess

import av
import pytest

import ableton_live_mcp._audio_decode as decoder
from ableton_live_mcp._audio_decode import decode_and_measure
from ableton_live_mcp.tools.audio_analysis import measure_audio


def make_audio(
    path, container, codec, sample_format, values=(0.25, -0.5), samples=4800, tone=False
):
    with av.open(str(path), "w", format=container) as output:
        stream = output.add_stream(codec, rate=48000)
        if codec == "vorbis":
            stream.options = {"strict": "experimental"}
        stream.layout = "stereo"
        stream.format = sample_format
        frame = av.AudioFrame(format=sample_format, layout="stereo", samples=samples)
        frame.sample_rate = 48000
        fmt = frame.format
        types = {"flt": "f", "dbl": "d", "s16": "h", "s32": "i"}
        base = fmt.name.rstrip("p")
        code = types[base]
        data = [
            [v * math.sin(2 * math.pi * 440 * i / 48000) if tone else v for i in range(samples)]
            for v in values
        ]
        if base not in ("flt", "dbl"):
            data = [[int(v * 2 ** (fmt.bits - 1)) for v in channel] for channel in data]
        if fmt.is_planar:
            for plane, channel in zip(frame.planes, data):
                plane.update(struct.pack("=" + code * samples, *channel))
        else:
            frame.planes[0].update(
                struct.pack("=" + code * samples * 2, *(v for pair in zip(*data) for v in pair))
            )
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    return path


@pytest.mark.parametrize(
    "extension,container,codec,fmt",
    [
        ("wav", "wav", "pcm_f32le", "flt"),
        ("wav", "wav", "pcm_f64le", "dbl"),
        ("aiff", "aiff", "pcm_s16be", "s16"),
        ("aif", "aiff", "pcm_s24be", "s32"),
        ("aifc", "aiff", "pcm_f32be", "flt"),
        ("flac", "flac", "flac", "s32"),
        ("m4a", "ipod", "alac", "s16p"),
    ],
)
def test_lossless_formats_and_channels(tmp_path, extension, container, codec, fmt):
    path = make_audio(tmp_path / f"two channels.{extension}", container, codec, fmt)
    before = hashlib.sha256(path.read_bytes()).digest()
    result = measure_audio(path)
    assert result["evidence"] == "measured_decoded_audio"
    assert result["sample_rate"] == 48000
    assert result["analyzed_seconds"] == pytest.approx(0.1)
    assert not result["partial"]
    for metric, expected in zip(result["channels"], (0.25, -0.5)):
        assert metric["dc_offset"] == pytest.approx(expected, abs=0.00001)
        assert metric["sample_peak_dbfs"] == pytest.approx(
            20 * math.log10(abs(expected)), abs=0.0001
        )
        assert metric["rms_dbfs"] == metric["sample_peak_dbfs"]
    assert hashlib.sha256(path.read_bytes()).digest() == before


@pytest.mark.parametrize(
    "extension,container,codec,fmt",
    [
        ("mp3", "mp3", "libmp3lame", "s16p"),
        ("ogg", "ogg", "vorbis", "fltp"),
        ("aac", "adts", "aac", "fltp"),
        ("m4a", "ipod", "aac", "fltp"),
    ],
)
def test_compressed_formats_measure_decoded_audio(tmp_path, extension, container, codec, fmt):
    path = make_audio(
        tmp_path / f"compressed.{extension}", container, codec, fmt, samples=12000, tone=True
    )
    result = measure_audio(path)
    assert result["codec"]
    assert result["bit_depth"] is None
    assert not result["partial"]
    assert result["analyzed_seconds"] > 0
    assert len(result["channels"]) == 2
    # A sine's RMS is amplitude/sqrt(2); allow codec padding/loss, not swapped channels.
    assert [c["rms_dbfs"] for c in result["channels"]] == pytest.approx(
        [-15.0515, -9.0309], abs=1.5
    )
    assert "Lossy decoding" in result["limitations"]


def test_float_headroom_is_not_clamped_and_partial_duration_is_honest(tmp_path):
    path = make_audio(tmp_path / "headroom.wav", "wav", "pcm_f32le", "flt", (1.25, -1.5))
    result = measure_audio(path, 0.05)
    assert result["partial"]
    assert result["duration_seconds"] is None
    assert result["analyzed_seconds"] == 0.05
    assert result["channels"][0]["sample_peak_dbfs"] == pytest.approx(
        20 * math.log10(1.25), abs=0.0001
    )
    assert result["channels"][1]["full_scale_samples"] == 2400
    assert not measure_audio(path, 0.1)["partial"]


def test_float_silence_and_nonfinite_samples(tmp_path):
    path = make_audio(tmp_path / "silence.wav", "wav", "pcm_f32le", "flt", (0, 0))
    assert measure_audio(path)["channels"][0]["rms_dbfs"] is None
    path = make_audio(tmp_path / "nan.wav", "wav", "pcm_f32le", "flt", (float("nan"), 0))
    with pytest.raises(ValueError, match="NaN"):
        measure_audio(path)


def test_decoder_rejects_invalid_files_urls_and_bad_limits(tmp_path):
    for path in ("https://example.com/song.mp3", tmp_path / "missing.wav"):
        with pytest.raises(ValueError):
            measure_audio(path)
    path = tmp_path / "fake.m4a"
    path.write_text("not media")
    with pytest.raises(ValueError, match="decode"):
        measure_audio(path)
    for limit in (0, 121, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="max_seconds"):
            measure_audio(path, limit)
    playlist = tmp_path / "playlist.m3u8"
    playlist.write_text("#EXTM3U\nhttps://example.com/song.mp3\n")
    with pytest.raises(ValueError):
        decode_and_measure(playlist, 1)


def test_decoder_timeout_is_an_error(tmp_path, monkeypatch):
    path = tmp_path / "slow.mp3"
    path.write_bytes(b"fixture")

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(args[0], 60)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError, match="processing limit"):
        measure_audio(path)


def test_sample_budget_reports_partial_result(tmp_path, monkeypatch):
    path = make_audio(tmp_path / "budget.wav", "wav", "pcm_f32le", "flt")
    monkeypatch.setattr(decoder, "MAX_SAMPLES", 48)
    result = decode_and_measure(path, 1)
    assert result["partial"] and result["limit_reason"] == "sample_budget"
    assert result["analyzed_seconds"] == 24 / 48000


@pytest.mark.parametrize("rate,layout,channels", [(48000, "7.1.4", 12), (768000, "stereo", 2)])
def test_unsupported_rates_and_channel_counts_are_rejected(tmp_path, rate, layout, channels):
    path = tmp_path / "too-many.wav"
    with av.open(str(path), "w", format="wav") as output:
        stream = output.add_stream("pcm_f32le", rate=rate)
        stream.layout = layout
        frame = av.AudioFrame(format="flt", layout=layout, samples=48)
        frame.sample_rate = rate
        frame.planes[0].update(bytes(48 * channels * 4))
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    with pytest.raises(ValueError, match="1–8 channels"):
        measure_audio(path)
