"""Fetch pinned Apache-2.0 SO-101 model assets; build the local manipulation fixture.

No code from the model repository is executed. Network access occurs only here,
never during studio startup or a simulation step.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
OUT = ROOT/"generated/so101"
REVISION = "3abca2e302625cec9429c49806121ea2855cef81"
SOURCE = f"https://raw.githubusercontent.com/google-deepmind/mujoco_menagerie/{REVISION}/robotstudio_so101"


def download(relative: str) -> Path:
    path = OUT/relative
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(SOURCE+"/"+relative, timeout=60) as response:
            content = response.read()
        path.write_bytes(content)
        print("Downloaded", relative)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = ["so101.xml", "LICENSE", "README.md", "CHANGELOG.md"]
    for name in files:
        download(name)
    robot = ET.parse(OUT/"so101.xml")
    for mesh in robot.findall("./asset/mesh"):
        relative = "assets/"+mesh.attrib["file"]
        download(relative)
        files.append(relative)
    previous = OUT/"source-manifest.json"
    hashes = {name: hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in files}
    if previous.is_file():
        old = json.loads(previous.read_text(encoding="utf-8"))
        if old["revision"] != REVISION or old["sha256"] != hashes:
            raise RuntimeError("Cached SO-101 assets differ from the pinned manifest; inspect before replacing them.")
    previous.write_text(json.dumps(dict(repository="https://github.com/google-deepmind/mujoco_menagerie",
        revision=REVISION, path="robotstudio_so101", license="Apache-2.0", sha256=hashes), indent=2)+"\n", encoding="utf-8")
    from forma_core.control.so101 import build_scene, export_scene_asset
    scene = build_scene(OUT)
    export_scene_asset(scene, ROOT/"web/assets/so101-scene.json")
    (OUT/"viewer-scene.json").write_bytes((ROOT/"web/assets/so101-scene.json").read_bytes())
    from forma_core.control.so101 import SO101Adapter
    from machine_packages import so101_package
    adapter = SO101Adapter(scene)
    package = so101_package(adapter, OUT)
    (OUT/"machine-package.json").write_text(package.model_dump_json(indent=2)+"\n", encoding="utf-8")
    adapter.close()
    print("SO-101 manipulation scene ready:", scene)


if __name__ == "__main__":
    main()
