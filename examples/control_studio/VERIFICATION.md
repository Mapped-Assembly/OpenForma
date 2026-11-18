# Verification record — 2026-11-18

Verified on this Windows checkout with the local virtual-adapter service at
`http://127.0.0.1:8766`. No physical machine or joystick was connected.

## Automated checks

| Check | Result |
|---|---|
| Python control/model/runtime and optional transport tests | 22 passed |
| JavaScript keyboard/gamepad input tests | 4 passed |
| Typed-core package metadata and backend-import boundary tests | 2 passed |
| Frontend `npm run build` | Passed |
| Native CAD | 22 valid solids, 74 features, 63 sampled poses; no sampled clashes |
| Deterministic XY benchmark | 6 coordinated segments, 361 samples; passed |
| Maximum step at 20 ms | X: 0.8 mm; Y: 0.6 mm, within floating-point tolerance |
| Maximum normalized coordination error | `1.1102230246251565e-16` |
| Typed MechanicalIR / HardwareIR package | Valid, 3 logical assembly instances, 22 body bindings |
| `forma-oss status --path examples/control_studio/generated/xy-stage --json` | Valid; no validation findings |
| Live reference-agent HTTP path | 5 segments passed; released/disarmed afterward |

## Browser verification

Desktop viewport: 1440 × 1000. Mobile layout viewport: 390 × 844.

- Independent X/Y keyboard jogging; key release holds the selected axis.
- Y-platform CAD transform inherits X motion and matches runtime coordinates.
- Six-segment coordinated path, Move Together, and manual path interruption.
- Target slider labels remain synchronized with programmatic moves.
- Switching to the original gate releases/disarms XY; the gate jogs through the
  same runtime. Switching back restores the two-axis controls and 22-body asset.
- Mocked `navigator.getGamepads()` verifies proportional X/Y movement (0.75
  Y/X displacement ratio for equal normalized input and 40/30 mm/s limits),
  neutral-to-Hold, disconnect-to-Hold and disabling joystick input.
- Narrow-screen pointer jogging and release-to-Hold; no horizontal overflow.
- No page errors during the recorded path and responsive checks.

Local rendered evidence:

- `evidence/xy-desktop.png`
- `evidence/xy-mobile.png`
- `generated/control-benchmark.json`
- `generated/live-agent-benchmark.json`
- `generated/xy-stage/cad-checks.json`
- `generated/xy-stage/forma-validation.json`

Generated evidence is ignored by Git. Reproduction commands are in the README.
Browser gamepad tests use a simulated device; physical controller compatibility,
physical actuation and measured feedback are not verified. CAD collision checking
is a discrete grid test, not a continuous swept-volume proof.

## Firmware SIL increment — 2026-11-18

- Built portable C++17 controller and native simulated I/O using existing MSVC
  19.43 / Visual Studio 2022. Compiler warnings are errors.
- 18 native/controller/adapter/service tests passed. Coverage includes fragmented
  frames, parser overflow recovery, atomic validation, stale sequences, local
  slew limits, watchdog latching, clock reversal, PWM, step direction/counts,
  simulated stalls, end switches, ownership and explicit reset.
- Existing 22 control/transport tests passed. Existing 22 firmware scaffold/IR
  tests passed after correcting the golden-file readers to explicitly use UTF-8
  on Windows; generated firmware and golden files were unchanged.
- Typed-package metadata and core/backend boundary checks passed.
- Python wheel built successfully with native source/CMake package data.
- Deterministic native gate path: 3 segments, 203 samples; maximum firmware step
  0.9 degrees at 20 ms. Native XY path: 6 segments, 366 samples; maximum steps
  0.8/0.6 mm at 20 ms. Both passed injected firmware watchdog expiry.
- Live HTTP agent benchmark: all 9 segments completed using **simulated plant
  arrival**, then released/disarmed both machines.
- Browser gate: 90-degree target produced 2000 µs PWM. With the plant stalled
  at 90 degrees, commanding 0 changed firmware PWM to 1000 µs while the CAD and
  simulated readout stayed at 90; measured feedback stayed null.
- Browser watchdog: dropped link latched `firmware_watchdog` and disabled output;
  reconnect did not resume; reset cleared the fixture and released/disarmed.
  Heartbeats now pause on observed FAULT rather than repeatedly sending rejected
  requests. The subsequent browser run had no page or console errors.
- Browser XY: six-segment path completed; X=20/Y=15 produced 1600/1200 signed
  steps, and Y-platform world transforms matched simulated X/Y positions.
- Firmware UI inspected at 1440 × 1000 and 390 × 844; no horizontal overflow.

Additional local artifacts:

- `generated/firmware/build-manifest.json`
- `generated/firmware-benchmark.json` (profiles, hashes, full output traces and live results)
- `evidence/firmware-before.png`
- `evidence/firmware-gate-stalled.png`
- `evidence/firmware-watchdog.png`
- `evidence/firmware-xy-desktop.png`
- `evidence/firmware-xy-mobile.png`

This executes native controller logic and simulated I/O, not an ESP32 image.
The virtual startup fixtures, servo calibration and steps/mm are explicit
assumptions. Homing, electrical edges, board peripherals and physical motion
remain outside this verification. See the firmware simulation guide for clock
semantics and the future board-port boundary.

## Control contract hardening — 2026-11-18

Public telemetry now exposes a separate owner ID instead of the session credential.
Adapter exceptions retain structured fault details through polling and release until
explicit reset. Python clients must select a completion source with `wait_for`;
examples and documentation use simulated arrival.

Verified on Linux with Python 3.12 and GNU C++ 14.2:

- Native SIL build: passed.
- Runtime, client, service and native SIL suite: 45 tests passed, no skips.
- Frontend: 5 tests passed, including ownership retention/takeover; bundle built.
- Ideal benchmark: 6 coordinated segments / 361 samples passed.
- Native firmware benchmark: gate 3 segments / 203 samples, XY 6 segments /
  366 samples, both watchdog checks passed.
- Regression coverage checks that observer telemetry cannot provide a session
  credential, public owner IDs cannot acquire leases, fault details survive release,
  omitted completion sources send no motion, and stalled/absent feedback cannot
  satisfy a wait by falling back to commanded position.

This follow-up did not repeat live-browser, live-HTTP benchmark, Windows, or
whole-repository validation.

## SO-101 contact-physics task — 2026-11-18

Verified locally on Windows, Python 3.12, MuJoCo 3.3.7, pinned Menagerie SO-101
`3abca2e302625cec9429c49806121ea2855cef81`:

- Explicit `setup_so101.py`: model, license/provenance manifest, task fixture and
  browser mesh export generated successfully. No runtime downloads.
- `python -m unittest tests.control.test_so101 tests.control.test_runtime tests.control.test_client tests.control.test_service tests.firmware.test_sil -v`:
  **51 passed**, no skips. The six physics/service tests also passed after the
  final adapter lifecycle change.
- `npm test`: **6 passed**, including reset draining in-flight commands before
  revoking ownership. `npm run build` and focused Python compileall passed.
- Deterministic `so101_benchmark.py`: **passed**, 12 observed stages, maximum
  lift **54.67 mm**, final cube center `[200.300, -104.310, 14.939]` mm.
- Live `so101_benchmark.py --live`: **passed**, 12 observed stages, maximum lift
  **54.78 mm**, final center `[200.353, -104.311, 14.939]` mm. This is a scripted
  reference using the agent client and simulated ground truth, not an autonomous
  learned/LLM policy.
- Browser at `http://127.0.0.1:8766`, 1440×1000: completed pick-and-place using
  visible XYZ fields/buttons and gripper buttons. Both jaw contacts observed,
  **54.89 mm** maximum lift, released/settled success. Rendered cube world
  coordinates matched physics telemetry exactly at lift and placement.
- Keyboard X step, Hold interrupt, individual wrist-roll slider, task reset,
  ownership release/reclaim, switch to XY and back: passed. Final reset left
  the arm at the initial fixture, disarmed, with simulation time zero.
- 390×844 mobile: no horizontal overflow; controls disabled while disarmed.
- Final browser interaction pass: zero page/console errors. One benign browser
  warning occurred when a newly opening WebSocket was replaced during rapid
  reset/reclaim. Earlier restart connection errors and the fixed reset heartbeat
  409 were excluded from that final pass, not mistaken for passing results.

Local evidence (ignored generated artifacts):

- `generated/so101/source-manifest.json`
- `generated/so101-benchmark.json`, `generated/so101-live-benchmark.json`
- `evidence/so101-before.png`, `evidence/so101-ready.png`
- `evidence/so101-manual-lift.png`, `evidence/so101-manual-success.png`
- `evidence/so101-mobile.png`

Linux/Windows CI now explicitly prepares this model and requires the contact
benchmark; that new CI configuration has not been run remotely. The repository-
wide suite was not repeated for this addition. Physical robot/joystick hardware,
camera perception and real SO-101 firmware were not tested.

## Identity-preserving machine packages — 2026-11-18

- Focused package, runtime, client, service, SO-101, firmware SIL and coding-agent
  skill suite: **80 Python tests passed**. Includes radians-to-degrees limit
  validation, original identity round trips, rehashed-but-inconsistent source
  rejection, CLI schema/import/validation, and additional package/viewer loading.
- Frontend: **7 tests passed**, including cancellation of a declared action while
  it is waiting for a lease; `npm run build` passed. `git diff --check` passed.
- Both generated packages pass `forma-core machine validate`. XY preserves
  **25 components and 15 joints**; SO-101 explicitly identifies an external model.
  Static validation reports `execution_verified: false`.
- Offline `action_benchmark.py`: **9/9 declared actions completed**, maximum
  lift **54.68 mm**, final cube `[200.298, -104.309, 14.939]` mm.
- Live `action_benchmark.py --live`: **9/9 completed**, maximum lift **55.12 mm**,
  final cube `[200.423, -104.314, 14.939]` mm. Every record carries the package and
  configuration hashes, run/command identity, simulated feedback and
  `contact-physics` provenance. Grasp records bilateral contact; placement records
  current `placement_settled: true` after retreat.
- The Python `ControlClient.act_and_wait()` helper also completed a live XY move
  and returned simulated position evidence with `ideal-kinematics` provenance.
- Browser at 1440×1000: completed pick/place through the generated action forms;
  final cube `[200.247, -104.301, 14.939]` mm. Rendered cube coordinates exactly
  matched physics telemetry. Action status and provenance appeared in the UI.
- Switching to XY and XY firmware SIL loaded both 22-body packaged viewers.
  Declared moves to X20/Y15 completed with their respective provenance. Hold
  interrupted an active SIL action and the UI reported `interrupted / held`.
- At 390×844, generated controls fit the viewport and are disabled when disarmed.
  Browser console: zero errors; one opening WebSocket was superseded by the
  ownership connection before it established. Automation waits were adjusted to
  wait for reset/machine-switch completion before claiming or arming.
- SO-101 was reset and left **released, disarmed, simulation time zero**.

Additional local evidence:

- `generated/action-evidence.json`
- `generated/action-live-evidence.json`
- `generated/xy-action-live-evidence.json`
- `evidence/package-disarmed.png`
- `evidence/package-contact-lift.png`
- `evidence/package-so101-desktop.png`
- `evidence/package-sil-actions.png`
- `evidence/package-mobile.png`

The nine-action reference is scripted, not an autonomous policy. These results
verify local simulation and package integration; physical hardware, the complete
repository suite and the new remote CI run remain unverified.

## Live agent visibility — 2026-11-18

- Added an always-visible execution strip above the viewer: controller/agent
  name, running action and parameters, completion/provenance, and stream status.
  A disconnected stream is explicitly labelled as a last-received pose.
- Versioned the changed bundle/CSS URLs after observing cached frontend assets
  during browser verification. Hard-refresh an already-open development page
  after rebuilding its frontend.
- Sent two declared tool moves through an independent Python `ControlClient`
  while the browser remained an observer. Captured **135 browser samples**, **56
  running-action samples**, and **49 distinct rounded rendered gripper poses**
  spanning **119.2 mm in Y**. Agent commands therefore changed both live action
  text and the rendered robot without browser motion commands.
- An initial probe used the body name `gripper` where the viewer expects a geom
  ID and returned null positions. The successful probe used actual `geom_24`.
- Frontend: 7 tests and build passed. Desktop 1440×1000 and mobile 390×844
  checked; mobile had no horizontal overflow. The robot was released/disarmed
  after the check; the cube remains placed on the target.

Evidence: `generated/live-observer-actions.json`,
`generated/live-browser-samples.json`, `evidence/live-agent-before.png`,
`evidence/live-agent-running.png`, and `evidence/live-agent-mobile.png`.

## Persistent machine task runner — 2026-11-18

- Added host-owned tasks, SDK subprocess, independent heartbeats, journals,
  goal entry, timeline, Stop and Take control. Task records survive browser and
  host restarts; interrupted execution is never automatically resumed.
- Focused runtime/client/service/package/task/SO-101/native firmware suite:
  **75 tests passed** before the additional subprocess test. Task suite then
  passed **10 tests**, including subprocess JSON-lines tool/result handling,
  rejected actions, successful evidence and forced worker cleanup. The protocol
  test uses a test fixture, not a real LLM.
- Frontend: **9 tests passed**; build and `git diff --check` passed. Both
  regenerated machine packages passed structural/artifact validation.
- Real browser at `http://127.0.0.1:8766/?tasks=e2e`, 1440×1000: authorized
  handoff, duplicate-start rejection (409), reload retaining the same active
  task ID with continuing heartbeats, Stop releasing/disarming, and Take control
  interrupting the task and acquiring a disarmed human session passed.
- Mobile 390×844: goal controls visible, no horizontal overflow. Screenshots
  saved for desktop, mobile, Stop and takeover. No task-worker subprocesses
  remained after terminal cleanup.
- Initial SDK attempts reached model dispatch but were blocked:
  `opencode/hy4-flash-free` and
  `opencode/deepseek-v4.1-flash-free` reported free usage limits;
  `gmi/gpt-6-astra` reported a missing API credential in the embedded host.
  Provider errors now surface from the SDK session instead of a generic
  ended-without-result error. Each failed attempt released/disarmed.
- User configured an OpenAI API key in the project-root `.env`. Studio now
  reloads that file plus ignored `.env.task` for every task; empty worker values
  do not mask project credentials. The subprocess test verifies inheritance,
  model override and absence of credentials from journals. **10 task tests passed**
  after this change. The API account rejected `gpt-6.2-astra`; its model listing
  confirmed `gpt-5.5`, which was selected explicitly for subsequent runs.

### Authenticated browser E2E — passed

- Real embedded OpenCode SDK + `openai/gpt-5.5`, started from the browser goal
  panel, completed **9/9 actions in 60 seconds**. Task
  `950328b503c7434abedfd62ec8033611` records **391 independent heartbeats**.
  The model discovered the package, selected actions, and compensated for the
  observed TCP-to-cube offset; no reference-controller fallback was used.
- Verified bilateral grasp, **55.27 mm actual simulated lift**, and released,
  settled placement with zero jaw contacts. Final cube center
  `[200.521, -98.310, 14.939]` mm, **1.77 mm XY target error**. Arm retreated
  clear; task finished released/disarmed. Physics/completion criteria unchanged.
- Reloaded the browser during execution; the host continued and the reconnected
  browser showed completion. Captured **589 browser samples during the task**,
  including **128 distinct rounded rendered gripper poses**. Full evidence link
  returned the completed journal with action evidence and contact-physics provenance.
- A second real LLM task on the XY package was stopped during its first move:
  `43228c09d4034890b227334aee790fe3`. Browser Stop changed the action from running
  to interrupted and released/disarmed immediately. A further task
  `f2af76e0e0cc4099a16242dc91b46885` was manually taken over during model reasoning;
  human ownership persisted, the machine stayed disarmed and axes remained stable.
- Desktop 1440×1000 and mobile 390×844 rendered the completed task; no horizontal
  overflow. Final browser pass: **zero console errors/warnings**. Earlier expected
  restart connection errors are excluded. No worker processes remained after
  completion/cancellation. SO-101's final placed scene is retained, released/disarmed.

Local evidence: `generated/tasks/*.json`, `generated/task-ui-checks.json`,
`evidence/tasks-before.png`, `evidence/task-stopped.png`,
`evidence/task-takeover.png`, `evidence/task-desktop.png`, and
`evidence/task-mobile.png`. Remote CI and physical hardware were not exercised.

Authenticated evidence: `generated/task-agent-browser-evidence.json`,
`generated/task-interruption-evidence.json`, the task journals above,
`evidence/task-agent-running.png`, `evidence/task-agent-lift.png`,
`evidence/task-agent-desktop-success.png`, `evidence/task-agent-mobile-success.png`,
`evidence/task-stop-live-action.png`, and `evidence/task-takeover-reasoning.png`.

### Pre-push portability cleanup

- Task reset availability now comes from installed adapter capabilities rather
  than frontend adapter-ID checks. Browser verified SO-101 (reset available),
  ideal XY (unavailable), and firmware SIL XY (available) on an isolated Studio
  instance at port 8767. Screenshot: `evidence/task-cleanup-capabilities.png`.
- Heartbeats follow the selected machine's timing configuration. A regression
  test uses a 120 ms watchdog to catch the former fixed 150 ms heartbeat.
- Removed the sample model default, robot-specific generic goal placeholder,
  temporary asset URL versions and SDK-specific wording in the reusable runner.
  Dependency/source revision pins and the SO-101 fixture configuration are intentional.
- Credential filename exclusions apply repository-wide, including a duplicate
  local `.env.task` in a nested folder. Project/worker credentials and task
  journals are confirmed ignored. Scanned 46 changed/new source and documentation
  files for local user paths and temporary viewer markers: no matches.
- **23 focused Python tests, 9 frontend tests, frontend build and diff check
  passed.** Browser capability checks passed with zero console errors; one
  opening WebSocket was superseded during machine switching. The isolated test
  server was stopped afterward; the original successful robot scene was retained.
