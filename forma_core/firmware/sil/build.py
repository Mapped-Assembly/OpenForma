"""Explicit native build; no download or compiler install is performed."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

SOURCE = Path(__file__).with_name("native")


def source_hash():
    digest = hashlib.sha256()
    for file in sorted(SOURCE.iterdir()):
        if file.is_file():
            digest.update(file.name.encode())
            digest.update(file.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def library_name():
    return "forma_firmware_sil.dll" if sys.platform == "win32" else "libforma_firmware_sil.dylib" if sys.platform == "darwin" else "libforma_firmware_sil.so"


def build(output: Path):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build_dir = output/"build"
    subprocess.run(["cmake", "-S", str(SOURCE), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release"], check=True)
    subprocess.run(["cmake", "--build", str(build_dir), "--config", "Release"], check=True)
    matches = list(build_dir.rglob(library_name()))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one built library; found {matches}")
    destination = output/library_name()
    shutil.copy2(matches[0], destination)
    manifest = dict(abi=1, source_sha256=source_hash(), library_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                    library=destination.name, execution="native C++ controller + simulated I/O", board_emulation=False)
    (output/"build-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    print(f"Built firmware SIL: {destination}")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
