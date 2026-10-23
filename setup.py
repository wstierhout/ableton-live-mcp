"""Optional native audio kernel; source installations retain a Python fallback."""

import os
import sys

from setuptools import Extension, setup

setup(
    ext_modules=[]
    if os.environ.get("ABLETON_MCP_PURE_PYTHON") == "1"
    else [
        Extension(
            "ableton_live_mcp._audio_native",
            ["ableton_live_mcp/_audio_native.c"],
            optional=True,
            extra_compile_args=["/O2", "/fp:precise"]
            if sys.platform == "win32"
            else ["-O3", "-ffp-contract=off"],
        )
    ]
)
