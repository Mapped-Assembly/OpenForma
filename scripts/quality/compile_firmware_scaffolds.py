"""Compile-mode check: materialize generated scaffolds and run PlatformIO.

Writes each example scaffold (the ``forma_core/firmware/examples.py`` fixtures)
to disk exactly as generated and runs ``pio run`` for its environment, proving
the deterministic output compiles without modification. Invoked by
``.github/workflows/firmware-compile.yml``; requires PlatformIO Core (``pio``)
on PATH.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from forma_core.firmware import (
    FirmwareScaffold,
    esp32_example_ir,
    generate_firmware,
    nano_example_ir,
)

# Board id → fixture that builds a hardware IR for that board.
EXAMPLES: dict[str, Callable[[], object]] = {
    "esp32dev": esp32_example_ir,
    "nanoatmega328": nano_example_ir,
}


def write_scaffold(board_id: str, build_root: Path) -> Path:
    """Generate the fixture scaffold and write its files under build_root."""

    ir = EXAMPLES[board_id]()
    scaffold = generate_firmware(ir)
    _assert_matches_board(scaffold, board_id)

    project_dir = build_root / board_id
    for path, content in scaffold.files.items():
        dest = project_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    return project_dir


def _assert_matches_board(scaffold: FirmwareScaffold, board_id: str) -> None:
    if scaffold.target is None or scaffold.target.board_id != board_id:
        raise ValueError(
            f"generated scaffold target {scaffold.target} does not match board {board_id}"
        )


def compile_project(project_dir: Path) -> None:
    """Run ``pio run`` for the (single) environment in the scaffold."""

    result = subprocess.run(["pio", "run", "-d", str(project_dir)])
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--board",
        choices=sorted(EXAMPLES),
        action="append",
        required=True,
        help="board id to compile; repeat for more than one",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="forma-firmware-") as tmp:
        build_root = Path(tmp)
        for board_id in args.board:
            project_dir = write_scaffold(board_id, build_root)
            print(f"=== compiling {board_id} ({project_dir}) ===", flush=True)
            compile_project(project_dir)
            print(f"=== {board_id}: OK ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
