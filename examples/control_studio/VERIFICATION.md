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
