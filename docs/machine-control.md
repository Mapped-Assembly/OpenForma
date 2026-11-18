# Reusable machine control

`forma_core.control` connects explicit machine capabilities to an input-independent
runtime. The browser is a client of that runtime; it never directly drives CAD
as a substitute for machine state.

## Delivered scope

- Versioned machine/axis definitions and protocol-v2 command validation.
- Named angular (`degree`) and linear (`mm`) axes, with per-axis rate limits.
- Independent moves/jogging and coordinated moves with common normalized progress.
- Single controlling session **per machine**, expiring leases, increasing sequence
  numbers, explicit takeover, watchdog, Hold and Disarm.
- Execution-adapter protocol and ideal virtual adapter with feedback provenance.
- Machine registry, standard-library agent client and optional local web host.
- One-axis servo gate and serial two-axis XY stage examples using the same code.
- Keyboard/pointer and browser Gamepad API input adapters.
- Optional [native firmware SIL adapter](firmware-simulation.md) with compiled
  C++ control logic, simulated servo/step I/O and a local firmware watchdog.

Board-specific physical firmware/drivers, torque/contact dynamics, acceleration/jerk planning,
homing, coordinated collision planning and hard-real-time guarantees are outside
this first implementation. A definition describes capability; it does not certify
that a physical machine can execute it.

## Run the examples

From the repository root (use `.venv\Scripts\python.exe` on Windows):

```sh
python -m pip install -e '.[control]'
cd examples/control_studio
npm ci
npm run build
python server.py --port 8766
```

Open **http://127.0.0.1:8766**. Select the XY stage or servo gate.

- **A** arms; **Space** holds; **Esc** disarms.
- XY: **Left/Right** jog X; **Up/Down** jog Y. Hold two arrows for a diagonal.
- Gate: Up/Right opens; Down/Left closes.
- Releasing a key sends zero velocity for that axis; other held axes continue.
- Sliders issue independent axis targets. **Move together** starts a coordinated
  move to all displayed targets. The benchmark button traces a rectangle and
  returns to center; manual input cancels its remaining steps.
- Selecting another machine releases/disarms the previous one first.

For a browser-recognized USB/Bluetooth controller, select the joystick input.
The first stick axis maps to the first machine axis; the second is inverted to
map stick-up to +Y. A 15% rescaled dead zone and proportional speed are applied.
Neutral, device disconnect, window blur or disabling joystick input requests
Hold. Arm/Disarm remain explicit panel or keyboard operations. The browser
may require a button press before exposing a device; arbitrary HID joysticks
are not necessarily exposed through `navigator.getGamepads()`.

## Machine definition and CAD binding

See [`definitions/xy-stage.json`](../examples/control_studio/definitions/xy-stage.json).
Each axis declares:

- Stable `axis_id`, human label, `unit`, minimum/maximum, neutral and maximum rate.
- Optional shared mechanical `component_ref`.
- Optional joint kind, direction, pivot, sign, parent axis and owned CAD body IDs.

The definition validator rejects duplicate axis/joint/body ownership, invalid
limits, incompatible joint units, dangling parents and cyclic kinematic ancestry.
The asset builder separately verifies that referenced bodies actually exist.
The generated benchmark package also validates component references against its
MechanicalIR. World-space neutral CAD geometry is nested according to these
bindings; Y moves locally while inheriting its X parent's translation.

A control definition is a sidecar (`format: forma.control-definition`, version 1)
referenced from project `assembly_metadata.control_definition`. It does not
silently reinterpret an arbitrary HardwareIR as an executable machine. No
existing hardware/domain schema was changed to assume a runnable control backend.

## Runtime semantics

Create `MachineRuntime(definition, adapter=...)`; omit the adapter only for a
virtual definition. Call `tick()` from a serialized service/control loop.

Protocol **v2** uses named maps instead of the early workspace's gate-specific
`angle_deg` packets. Capabilities publish the full command JSON Schema.

| Operation | Additional fields | Meaning |
|---|---|---|
| `arm` | none | Enable at last command or declared neutrals; cancel old unfinished motion on resume |
| `move` | `targets`, optional `rates`, `coordinated` | Absolute named-axis targets |
| `jog` | `velocities` | Signed per-axis rates toward the corresponding travel bound; zero holds that axis |
| `hold` | optional `axes` | Freeze selected axes, or all if omitted |
| `disarm` | none | Disable adapter output and clear targets |
| `heartbeat` | none | Refresh valid-command traffic without resuming motion |

Every packet also needs version 2, active session ID, a fresh server-issued lease,
and a strictly increasing sequence. Limits and all axis references are validated
**before any part of a multi-axis command is applied**.

For coordinated movement, duration is the maximum of `distance / requested_rate`
over the selected axes. All selected axes interpolate with the same progress, so
they arrive together without exceeding their respective command-rate limits.
Interrupting one member freezes **the entire previous coordinated group**, then
applies the new request. Independent axes outside that group keep their motion.
This prevents an abandoned diagonal from partially continuing unexpectedly.

The integration time step is capped at `update_ms`; scheduler delays never cause
a catch-up jump. Before advancing motion, the runtime checks for link loss.
Default valid-command timeout is 1000 ms; fresh leases/telemetry reads alone do
not count as command traffic. Reconnect/heartbeat never resumes a lost-link plan.

## Feedback and execution adapters

`MachineAdapter.apply(commands, enabled)` and `read()` separate execution from
input and transport. A virtual adapter may report simulated position, never a
physical measurement. A physical adapter may report measurements or null, never
simulated position. Faulting adapters latch `FAULT`, clear plans/feedback and
attempt to disable output; they cannot be rearmed without correcting/restarting
the runtime. Hardware must independently enforce its local limits/watchdog.

Telemetry keeps `commanded`, `target`, `simulated` and `measured` separate per
axis. On Disarm, the virtual position becomes unknown; the viewer retains a
clearly labeled last-command/reference pose. `command_motion_complete` is a
command-planning result, not measured arrival or obstruction detection.

Adapter calls must be bounded and nonblocking for the service loop. Do not put
LLM reasoning or slow remote I/O inside the timing loop. Agents submit commands
through the client/API and observe results while the runtime owns timing.

## Agent API

The optional example host exposes `/api/control/v2/machines` and per-machine
`capabilities`, `claim`, `lease`, `command`, `state`, `stream` and `release`.
See local `/docs`. It binds loopback only. Sessions coordinate local clients;
they are not a deployment authentication system.

```python
from forma_core.control import ControlClient

machine = ControlClient("http://127.0.0.1:8766", "xy-stage")
machine.claim("My agent")  # Release the browser first, or explicitly use takeover=True.
try:
    machine.command("arm")
    state = machine.move_and_wait(
        {"x": 20, "y": -15}, rates={"x": 30, "y": 20}, coordinated=True, wait_for="simulated"
    )
    print(state["axes"])
finally:
    machine.release()
```

The client supplies heartbeats during `move_and_wait`. Other long-running callers
must send heartbeats themselves. A controller/agent owns each machine until it
releases or another client explicitly takes over.

`move_and_wait` requires an explicit `wait_for` argument; select `wait_for="simulated"`
with an appropriate quantization tolerance to wait for the firmware simulator's
plant, or `wait_for="measured"` for actual measurements when available. Optional
`execution` diagnostics and `execution_fault` keep adapter state separate from
the host planner. Reported adapter faults disable output and latch runtime FAULT.
Choose `wait_for="commanded"` only when host-plan completion is sufficient.
Existing callers that omitted `wait_for` must now choose a source; omission fails
before sending motion. No source falls back to another source.

Adapter exceptions expose `execution_fault="adapter_exception"` and persistent
`execution_error` fields (`reason`, exception `type`, and `message`). These
details survive later polls and release and clear only on explicit adapter reset.

Public owner telemetry contains only `owner_id`, `name`, and `kind`. The claim
response separately returns the private `session_id` used for control and the
public `owner_id` used to detect ownership changes. Public IDs cannot obtain
leases or authorize commands. The local service still permits explicit takeover;
ownership is coordination, not user authentication.

## Benchmarks and verification

```sh
python -m unittest discover -s tests/control -v
python examples/control_studio/benchmark.py
python examples/control_studio/build_assets.py --force
python examples/control_studio/package_benchmark.py
```

In `examples/control_studio`: `npm test` exercises keyboard/joystick semantics;
`python agent_path.py` exercises a running service via the agent client.

The XY benchmark is a **virtual linear stage**: 22 native solids, X ±40 mm and
Y ±30 mm, 40/30 mm/s maximum command rates. The physical drive, motors, sensors
and actual bearings are not selected. The 63-pose CAD grid checks sampled
intersections and nested translations, not continuous swept collision or loads.
See the example README for generated artifact locations and source provenance.
