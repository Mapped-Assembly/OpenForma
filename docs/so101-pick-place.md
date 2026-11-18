# SO-101 manual and agent pick-and-place

Control Studio now supports a six-axis SO-101 physics fixture. The same Forma
runtime accepts browser, joystick and Python agent commands, with exclusive
ownership, leases, monotonic command sequences, bounded rates and interruption.
MuJoCo supplies gravity, friction, collision response and simulated joint state.
The free cube is moved by jaw contact: no weld, attachment or teleport action is
used by the controller.

## Setup and launch

From the OpenForma repository:

```bash
python -m pip install -e ".[control,robot-sim]"
python examples/control_studio/setup_so101.py
cd examples/control_studio
npm ci
npm run build
cd ../..
python examples/control_studio/server.py --port 8766
```

On Windows, use `.venv\Scripts\python.exe` for the Python commands. Open
<http://127.0.0.1:8766>. Select **SO-101 · Pick and Place** if necessary.

The setup script downloads the Apache-2.0 MuJoCo Menagerie
[`robotstudio_so101` description](https://github.com/google-deepmind/mujoco_menagerie/tree/3abca2e302625cec9429c49806121ea2855cef81/robotstudio_so101)
at revision `3abca2e302625cec9429c49806121ea2855cef81`. It retains upstream
`LICENSE`, README and changelog, records SHA-256 hashes in
`examples/control_studio/generated/so101/source-manifest.json`, and refuses a
cache that differs from an existing manifest. It derives `pick-place.xml` and
exports `web/assets/so101-scene.json`. Both directories are local generated
assets. No upstream Python code is executed; startup and tests never download.
The optional machine appears only when MuJoCo and both generated assets exist.

## Try it manually

1. **Take control**, then **Arm machine**. These are separate actions.
2. **Open gripper**, **Fill above cube**, then **Move tool to XYZ**.
   The initial hover is `[220, 25, 70]` mm.
3. Change **Z** to **15** and press **Move tool to XYZ**. Wait for the tool to
   arrive. Press **Close gripper** and wait for **BOTH JAWS IN CONTACT**.
4. Change **Z** to **70**, then move. Look for **LIFT VERIFIED**.
5. **Fill above target**, then move. The target hover is `[200, -100, 70]` mm.
6. Change **Z** to **15** and move. **Open gripper**, wait for it to open,
   then move back up to **Z 70**.
7. The task reports **SUCCESS · placed and released** after the cube settles.

The Fill buttons only fill coordinates. You decide when to execute each move.
The cube can be missed, pushed or dropped. **Reset task** restores the initial
virtual fixture, clears the score, releases control and disarms; claim and arm
again for another trial.

- Arrows tap X/Y by 5 mm; Page Up/Down tap Z. A tap completes one bounded move.
- O opens; C closes. Space holds the current plan; Escape disarms.
- Pointer arrows and Z buttons provide the same incremental moves.
- Expand **Individual axis controls** for all five arm joints plus gripper.
- A browser gamepad maps its axes to shoulder pan/lift; remaining axes use
  sliders/tool controls. Physical gamepad hardware has not been tested here.
- Disarm **pauses this virtual fixture**. It is not a model of removing power
  from a gravity-loaded real arm. Hold and link-loss retain position actuation.

Tool moves solve bounded, top-down inverse kinematics and then execute the
ordinary coordinated **joint-interpolated** command. The tool path is not
guaranteed straight or collision-free. Use 70 mm transport height around this
fixture: the upstream 95° wrist-flex limit makes many 120 mm top-down targets
unreachable. Rejected IK requests send no motion. Joint controls remain useful
for orientations the top-down helper does not handle.

## What counts as success

- Cube: 30 mm sides, 25 g, initially centered at `[220, 25, 15]` mm.
- Lift: cube center rises more than 35 mm above its resting height while both
  jaw bodies contact it. This evidence is latched for the trial.
- Placement: cube center within 25 mm of the target XY, within 8 mm of resting
  height; gripper opening greater than 45°; no jaw contact; translational speed
  below 10 mm/s and angular speed below 0.1 rad/s continuously for 0.5 simulated
  seconds. A prior verified lift is required.
- Success is a latched completed-trial result; Reset starts a new trial. Simply
  pushing a cube onto the target does not satisfy the lift requirement.

The viewer renders upstream visual meshes in the MuJoCo rigid-body hierarchy.
It consumes live simulated body poses, not commanded joint targets. Collision
geometries are the upstream simplified primitives/meshes and can differ from
the visual surfaces. Joint targets, simulated joint feedback and measured
feedback are distinct; measured feedback remains null.

## Agent interface

The agent uses the same `/api/control/v2/machines/so101` routes as the browser.
Release browser ownership before starting an agent.

```python
from forma_core.control.client import ControlClient

robot = ControlClient("http://127.0.0.1:8766", "so101")
robot.claim("My agent")
try:
    robot.command("arm")
    targets = robot.solve_tool([220.0, 25.0, 70.0])
    robot.move_and_wait(targets, wait_for="simulated", tolerance=1.0)
    task = robot.state()["execution"]["details"]
    print(task["tcp_mm"], task["cube_mm"], task["contact_sides"])
finally:
    robot.release()
```

`POST /ik` accepts `{"target_mm": [x, y, z]}` and returns five joint targets
with `executed: false`. It is a pure solver and requires no ownership; executing
its result requires the standard authenticated, lease/sequence-controlled
`move` command. `capabilities.tool_ik` advertises this helper. Gripper opening is
an ordinary named-axis move. Closing on a cube cannot reach a 0° joint target:
wait for **bilateral contact**, not for simulated gripper position to equal zero.
The command plan finishing alone is not evidence of a successful grasp.

Observations include tool/cube positions, tool up direction, cube orientation
and velocity, contacts, lift, settle time, success and simulation time. They are
explicitly **simulated ground truth**, not camera perception. Long-running
agents must send heartbeats; `move_and_wait` does so while waiting. Use
`reset_simulation()` only while owning the machine, then claim again.

### Scripted reference controller

```bash
python examples/control_studio/so101_benchmark.py
python examples/control_studio/so101_benchmark.py --live
```

The first command drives the shared runtime with a deterministic clock; the
second drives the running HTTP service. Live mode claims an idle machine,
resets the fixture, then executes the trial and releases it on exit. It does
not take over an occupied machine. Reports are written to
`examples/control_studio/generated/so101[-live]-benchmark.json`.

This is a **scripted reference**, not an autonomous learned/LLM policy. It
provides a reproducible baseline and agent-facing commands/observations for
the next experiment. It checks actual TCP arrival, bilateral contact, verified
lift and released/settled placement.

## Verification and scope

```bash
python -m unittest tests.control.test_so101 -v
```

Six optional physics/service tests cover a complete contact-only trial, a free
cube falling, paused disarmed state, rejection of unearned placement, pure and
bounded IK, ownership and reset semantics. Missing local assets skip these
tests; the benchmark itself fails without setup. CI explicitly prepares the
pinned model and requires benchmark success on Linux and Windows.

This fixture uses upstream position-actuator gains, limits, inertias and contact
geometry, with a 2 ms physics timestep. Each advance is capped at 20 ms;
simulation can run slower than wall time on a busy host. This is a manipulation
physics benchmark, separate from the gate/XY compiled firmware SIL. It does not
model SO-101 serial firmware, camera perception, motor calibration or hardware
accuracy. No physical arm is connected or validated.
