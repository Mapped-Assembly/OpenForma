"""Install the two pinned upstream robot descriptions independently of any demo."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

# Only the licensed model directories are copied into the runtime.
SOURCES = (
    (
        "astra",
        "fedecomi04/opensource-spinal-quadruped",
        "41dab01c906a14dd374a10fb7f234179b8c7736c",
        "ros2_ws/src/mujoco_interface/model",
        "LICENSE",
    ),
    (
        "so101",
        "viam-devrel/so-101",
        "e571ed96ec056169650e858413d49438ffbd3543",
        "assets/urdf",
        "assets/urdf/SO-ARM100-LICENSE",
    ),
)


def validate_assets(destination: Path) -> dict[str, str]:
    """Check installed revision declarations against this adapter's supported models.

    This checks compatibility metadata, not cryptographic integrity of local files.
    """
    revisions: dict[str, str] = {}
    for name, _repo, revision, _subtree, _license in SOURCES:
        marker = destination / name / "REVISION"
        if not marker.is_file() or marker.read_text().strip() != revision:
            raise ValueError(f"Unsupported asset revision for {name}; install pinned rover assets")
        revisions[name] = revision
    return revisions


def install(destination: Path) -> None:
    """Fetch exact commits into temporary checkouts and retain source licenses."""
    destination.mkdir(parents=True, exist_ok=True)
    for name, repo, revision, subtree, license_path in SOURCES:
        target = destination / name
        if (target / "REVISION").is_file() and (target / "REVISION").read_text().strip() == revision:
            continue
        if target.exists():
            raise ValueError(f"Asset directory already exists with unknown revision: {target}")
        with tempfile.TemporaryDirectory(dir=destination) as directory:
            work = Path(directory) / "upstream"
            subprocess.run(["git", "init", "-q", str(work)], check=True)
            git = ["git", "-C", str(work)]
            subprocess.run(
                [
                    *git,
                    "fetch",
                    "--depth=1",
                    "--filter=blob:none",
                    f"https://github.com/{repo}.git",
                    revision,
                ],
                check=True,
            )
            subprocess.run([*git, "checkout", "FETCH_HEAD", "--", subtree, license_path], check=True)
            staged = work / subtree
            if (work / license_path).parent != staged:
                shutil.copy2(work / license_path, staged / "LICENSE")
            (staged / "REVISION").write_text(revision + "\n")
            staged.rename(target)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    install(cli.parse_args().directory)
