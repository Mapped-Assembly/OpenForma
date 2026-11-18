# Forma Control Studio — reusable machine control

The original servo gate and a new **two-axis XY stage** use `forma_core.control`
without machine-specific runtime logic. The browser is an optional client.
An optional **SO-101 arm** adds a contact-physics pick-and-place task through
the same control runtime; see the [manual/agent guide](../../docs/so101-pick-place.md).

**[Open the running studio](http://127.0.0.1:8766)** ·
[Architecture/API guide](../../docs/machine-control.md)

[Verification results](VERIFICATION.md)

[Machine packages and declared actions](../../docs/machine-packages.md) preserve
OpenCAD identities, bind installed adapters, and report observed completion.
The **Declared machine actions** section is generated from a machine's package.

## Run a machine goal

Select a packaged machine, enter a goal under **Give it a goal**, and click
**Run task**. The browser transfers its control session to a persistent host
runner. The timeline and live viewer show the agent's actions and feedback.
Reloading or closing the browser does not stop the task.

- **Stop task** releases/disarms immediately; **Take control** interrupts the
  task and gives control to the browser, with explicit Arm required.
- **Task options** allows a simulation reset and an explicit `provider/model`.
  Otherwise the worker uses the embedded OpenCode host's default model.
- `npm ci` installs pinned OpenCode SDK/plugin 2.3.2 and Bun 1.4.4. Provider
  credentials must be available to that embedded host (for example through
  OpenCode provider configuration/environment). Signing in to a separate
  OpenCode service does not necessarily authenticate this embedded host.
- Provider/auth/quota errors appear in the timeline and release control. There
  is no scripted fallback. The default free model is subject to provider limits.
- **View execution evidence** opens the task journal, including before/after
  observations, action records, package hash and provenance. Journals are saved
  under `generated/tasks/`; unfinished tasks become interrupted after a host
  restart and are not resumed automatically.

Manual operation uses the same runtime and simulation. If another browser owns
the machine, explicitly take control before starting a task.

For an OpenAI API key, copy `.env.task.example` to `.env.task` in this directory
and fill in `OPENAI_API_KEY` locally. Choose a model your API account supports in
`FORMA_TASK_MODEL`; the browser's optional Model field overrides it. The ignored
file is reloaded for each task, so credential changes do not need a Studio
restart. The key goes only into the worker's environment, never task telemetry
or evidence. Regular OpenCode provider configuration/environment also works.
Studio also reads the repository-root `.env`; nonempty values in `.env.task`
override it. A key already configured in the project therefore works directly.

## Executable firmware simulation

**[Firmware SIL guide](../../docs/firmware-simulation.md)** — compiled C++
controller logic, simulated servo PWM/step outputs, local watchdog and fault
injection. Build the optional native library from the repository root:

```sh
python -m forma_core.firmware.sil.build --output examples/control_studio/generated/firmware
```

Then restart the studio and select **Servo Gate · Firmware SIL** or
**Two-axis XY Stage · Firmware SIL**. The firmware panel lets you drop the link,
stall the first actuator, and reset. CAD follows the simulated plant, so a stall
can visibly disagree with the command. This is software-in-the-loop, not ESP32
board emulation. Reset assumes a known virtual startup fixture; it does not home
physical hardware.

Run `python examples/control_studio/firmware_benchmark.py --live` from the repo
root to check both compiled controllers through the live API (release browser
control first).

## Start

From the repository root, with the project environment active:

```sh
python -m pip install -e '.[control]'
cd examples/control_studio
npm ci
npm run build
python server.py --port 8766
```

Or on this Windows checkout run `Start Studio.cmd`. It uses the repository's
`.venv` and opens the browser. The compiled JavaScript is generated under
`web/dist` and ignored by Git; rebuild it after changing browser sources.
All browser dependencies are bundled locally; no CDN is needed at runtime.

## Controls

Press **A** to arm. Hold arrows to move, release to hold; **Space** holds all
axes and **Esc** disarms. XY uses Left/Right for X and Up/Down for Y. Gate uses
Up/Right to open and Down/Left to close. Click outside a slider/select to use
global keyboard controls. Pointer jog buttons are available on narrow screens.

**Move together** synchronizes axis targets. **Run benchmark path** performs six
coordinated moves around a rectangle and back to the center; manual input
interrupts the remaining path. Switching machines releases/disarms the old one.

Select **USB / Bluetooth joystick** for browser Gamepad API input. First two
stick axes, 15% dead zone, proportional speed, neutral/disconnect-to-Hold.
The adapter has automated tests and is exercised with simulated browser
gamepad samples; an actual device is still needed to verify your hardware mapping.

## Benchmark artifacts

- Definitions: [`xy-stage.json`](definitions/xy-stage.json), [`servo-gate.json`](definitions/servo-gate.json).
- Native editable geometry: [`cad/xy_stage.py`](cad/xy_stage.py).
- Browser meshes: `web/assets/xy-stage.json` and `web/assets/servo-gate.json`.
- Native STEP/STL, tree and joints: `generated/xy-stage/`.
- Named assembly: `generated/xy-stage/xy-stage-hierarchy.step`.
- Typed MechanicalIR, hierarchy, bindings and Forma project: same generated folder.
- Control benchmark: `generated/control-benchmark.json`.
- Live agent-client test: `generated/live-agent-benchmark.json`.
- Browser verification evidence: `evidence/`.

The XY source builds 22 solids in stationary frame, X carriage and Y platform
groups. X carries the entire Y mechanism. Its -40..40 mm and -30..30 mm ranges
exercise real serial transformations. No motors or physical drive are implied
by these ideal prismatic joints. All dimensions are conceptual assumptions.
The three component references B1/X1/Y1 are logical assembly rollups, not a
procurement BOM. Actual support/retention, bearing fit and load capacity remain
unverified. Native CAD sampling checks 63 positions at 10 mm grid spacing.

The gate mesh is imported from the prior verified OpenCAD export; its
`source_step_sha256` is retained in the asset. It has 20 bodies and the original
shaft/pivot convention. The former standalone workspace controller on port 8765
is preserved. Studio uses **protocol v2** with named axis maps; it does not
pretend the old angle-specific wire protocol is interchangeable.

## Rebuild / verify

From repository root:

```sh
python -m unittest discover -s tests/control -v
python examples/control_studio/build_assets.py --force
python examples/control_studio/benchmark.py
python examples/control_studio/package_benchmark.py
```

CAD rebuild requires the existing managed OpenCAD/OCCT environment. `build_assets.py`
reuses the included gate mesh; `--gate-mesh <file>` explicitly replaces it with a
new verified mesh export. Browser operation itself does not require OpenCAD.

From this directory:

```sh
npm test
npm run build
python agent_path.py
```

Release the browser before running the agent test, or explicitly pass
`--takeover`. The test uses the same API as human input and disarms/releases
after completion. All feedback in these examples is simulated; physical
measurement remains null. This is soft-real-time control, not a real-time OS.

## SO-101 manipulation benchmark

For a manual contact-physics pick-and-place task and the same interface for
agents, see [SO-101 setup, manual trial and agent API](../../docs/so101-pick-place.md).
Install `.[control,robot-sim]`, run `setup_so101.py`, rebuild the frontend and
restart Studio. SO-101 becomes the initial machine when its local assets exist.
