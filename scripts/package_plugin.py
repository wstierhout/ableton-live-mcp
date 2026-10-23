"""Create a standalone Codex plugin archive. Does not install or publish anything."""

import argparse
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/ableton-live"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    version = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())["version"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"ableton-live-codex-{version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Plugin archives must be self-contained: {path}")
            if path.is_file():
                archive.write(path, Path("ableton-live") / path.relative_to(PLUGIN))
    print(target)
