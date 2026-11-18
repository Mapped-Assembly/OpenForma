"""Launch a scene in an independent project using installed Forma control."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .host import create_app


def serve(
    *,
    scenario: Path | None = None,
    assets: Path | None = None,
    output: Path | None = None,
    worker: Path | None = None,
) -> None:
    """Launch a loopback HTTP host with explicit portable inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    for key, default in (("scenario", scenario), ("assets", assets), ("output", output), ("worker", worker)):
        parser.add_argument("--" + key, type=Path, default=default, required=default is None)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    app = create_app(
        args.scenario, args.assets, args.output, worker=args.worker.resolve(), env_files=tuple(args.env_file)
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    serve()
