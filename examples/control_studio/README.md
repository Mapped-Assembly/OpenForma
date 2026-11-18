# Forma Control Studio — two machines, one runtime

The original servo gate and a new **two-axis XY stage** use `forma_core.control`
without machine-specific runtime logic. The browser is an optional client.

**[Open the running studio](http://127.0.0.1:8766)** ·
[Architecture/API guide](../../docs/machine-control.md)

[Verification results](VERIFICATION.md)

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
