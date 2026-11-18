"""Launch the sample kitchen with the shared Forma workcell host."""

from pathlib import Path

from forma_core.control.rover_scene.__main__ import serve

HERE = Path(__file__).resolve().parent
DEFAULT = HERE / "scenario.json"
WORKER = HERE.parent / "control_studio/task_worker.mjs"

if __name__ == "__main__":
    serve(scenario=DEFAULT, assets=HERE / "generated/robots", output=HERE / "generated/run", worker=WORKER)
