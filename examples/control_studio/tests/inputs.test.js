import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeStick, keyboardIntent, gamepadIntent, GamepadSource } from '../web/inputs.js';

test('dead zone, saturation and invalid joystick values', () => {
  assert.equal(normalizeStick(.14), 0);
  assert.equal(normalizeStick(-.15), 0);
  assert.equal(normalizeStick(2), 1);
  assert.equal(normalizeStick(NaN), 0);
  assert.ok(Math.abs(normalizeStick(.575) - .5) < 1e-10);
});
test('two simultaneous keys and opposed keys', () => {
  assert.deepEqual(keyboardIntent(new Set(['ArrowRight', 'ArrowUp']), ['x', 'y']), {x:1,y:1});
  assert.deepEqual(keyboardIntent(new Set(['ArrowRight', 'ArrowLeft']), ['x', 'y']), {x:0,y:0});
  assert.deepEqual(keyboardIntent(new Set(['ArrowUp']), ['gate']), {gate:1});
});
test('joystick axes map to named machine axes and invert screen Y', () => {
  assert.deepEqual(gamepadIntent({axes:[1,-1]}, ['x','y']), {x:1,y:1});
});
test('neutral, disconnect and disabled input all emit one stop', () => {
  const emitted=[], status=[];
  const source=new GamepadSource(['x','y'], v=>emitted.push(v), s=>status.push(s));
  source.sample({connected:true,axes:[1,0]},true);
  source.sample({connected:true,axes:[0,0]},true);
  assert.deepEqual(emitted.at(-1),{x:0,y:0});
  const count=emitted.length;
  source.sample({connected:true,axes:[0,0]},true);
  assert.equal(emitted.length,count);
  source.sample({connected:true,axes:[1,1]},true);
  source.sample(null,true);
  assert.deepEqual(emitted.at(-1),{x:0,y:0});
  source.sample({connected:true,axes:[-1,0]},true);
  source.sample({connected:true,axes:[-1,0]},false);
  assert.deepEqual(emitted.at(-1),{x:0,y:0});
});
