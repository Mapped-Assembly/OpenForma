// Device-independent normalized intent. No networking or scene mutation here.
export function normalizeStick(value, deadzone = .15) {
  if (!Number.isFinite(value) || Math.abs(value) <= deadzone) return 0;
  return Math.sign(value) * Math.min(1, (Math.abs(value) - deadzone) / (1 - deadzone));
}

export function keyboardIntent(codes, axes) {
  const positiveX = codes.has('ArrowRight') || (axes.length === 1 && codes.has('ArrowUp'));
  const negativeX = codes.has('ArrowLeft') || (axes.length === 1 && codes.has('ArrowDown'));
  const result = { [axes[0]]: Number(positiveX) - Number(negativeX) };
  if (axes[1]) result[axes[1]] = Number(codes.has('ArrowUp')) - Number(codes.has('ArrowDown'));
  return result;
}

export function gamepadIntent(pad, axes, deadzone = .15) {
  return Object.fromEntries(axes.map((axis, i) => [axis, (normalizeStick(pad?.axes?.[i] ?? 0, deadzone) * (i === 1 ? -1 : 1)) || 0]));
}

export class GamepadSource {
  constructor(axes, emit, status) {
    this.axes = axes; this.emit = emit; this.status = status; this.moving = false; this.last = {};
  }
  // Called at 20 Hz by the browser, or deterministically by the test harness.
  sample(pad, active) {
    if (!active || !pad?.connected) {
      if (this.moving) this.emit(Object.fromEntries(this.axes.map(a => [a, 0])));
      this.moving = false; this.last = {};
      this.status(pad?.connected ? 'Joystick paused' : 'No joystick connected');
      return;
    }
    const intent = gamepadIntent(pad, this.axes);
    const moving = Object.values(intent).some(v => v !== 0);
    if (moving || this.moving) {
      if (this.axes.some(a => Math.abs(intent[a] - (this.last[a] ?? 0)) > .02) || moving !== this.moving) this.emit(intent);
    }
    this.moving = moving; this.last = intent;
    this.status(`${pad.id || 'Joystick'} · ${moving ? 'jogging' : 'neutral'}`);
  }
}
