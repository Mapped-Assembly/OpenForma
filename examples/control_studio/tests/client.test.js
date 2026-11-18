import test from 'node:test';
import assert from 'node:assert/strict';
import { ControlClient } from '../web/client.js';

test('task handoff drains commands and revokes browser control before posting', async t => {
  t.mock.method(globalThis, 'setInterval', () => 0);
  const client = new ControlClient('fixture');
  client.session = 'private'; client.ownerId = 'public'; client.online = true;
  let drain, posted = false;
  client.queue = new Promise(resolve => { drain = resolve; });
  client.connect = () => {};
  client.request = async (path, body) => {
    assert.equal(path, '/tasks');
    assert.deepEqual(body, {goal:'Move X', reset:true, session_id:'private'});
    assert.equal(client.session, null); posted = true; return {task_id:'task'};
  };
  const pending = client.startTask('Move X', {reset:true});
  assert.equal(client.ownerId, null); assert.equal(posted, false);
  drain(); assert.equal((await pending).task_id, 'task');
  assert.equal(await client.command('arm'), false);
  await client.dispose();
});

test('failed or ambiguous task starts restore only confirmed original ownership', async t => {
  t.mock.method(globalThis, 'setInterval', () => 0);
  for (const owner of ['public', 'agent', null]) {
    const client = new ControlClient('fixture');
    client.session = 'private'; client.ownerId = 'public'; client.connect = () => {};
    client.request = async path => {
      if (path === '/tasks') throw Error('Start response lost');
      assert.equal(path, '/state'); return {telemetry:{owner:owner ? {owner_id:owner} : null}};
    };
    await assert.rejects(client.startTask('Move'), /Start response lost/);
    assert.equal(client.session, owner === 'public' ? 'private' : null);
    assert.equal(client.lease, null);
    client.session = null; await client.dispose();
  }
});

test('public owner identity preserves control and detects takeover without a session leak', async t => {
  t.mock.method(globalThis, 'setInterval', () => 0);
  const previous = {WebSocket: globalThis.WebSocket, location: globalThis.location};
  globalThis.location = {href: 'http://127.0.0.1:8766/'};
  globalThis.WebSocket = class {
    close() {}
    frame(ownerId) {
      this.onmessage({data: JSON.stringify({telemetry: {owner: {owner_id: ownerId, name: 'test', kind: 'human'}}})});
    }
  };
  const client = new ControlClient('xy');
  client.emit = () => {};
  client.request = async () => ({session_id: 'private', owner_id: 'public', lease_id: 'lease', valid_for_ms: 500});
  try {
    await client.claim();
    client.ws.frame('public');
    assert.equal(client.session, 'private');
    assert.equal(client.ownerId, 'public');
    client.ws.frame('replacement');
    assert.equal(client.session, null);
    assert.equal(client.ownerId, null);
    assert.equal(client.lease, null);
    await client.claim();
    await client.release();
    assert.equal(client.ownerId, null);
  } finally {
    await client.dispose();
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});

test('reset drains in-flight commands and invalidates ownership before revocation', async t => {
  t.mock.method(globalThis, 'setInterval', () => 0);
  const client = new ControlClient('so101');
  client.session = 'private'; client.ownerId = 'public'; client.online = true;
  let finish, sent = false, reconnected = false;
  client.queue = new Promise(resolve => { finish = resolve; });
  client.request = async (path, body) => {
    assert.equal(path, '/simulation'); assert.deepEqual(body, {session_id: 'private', op: 'reset'});
    assert.equal(client.session, null); sent = true; return {reset: true};
  };
  client.connect = () => { reconnected = true; };
  const reset = client.resetSimulation();
  assert.equal(client.session, null); assert.equal(client.ownerId, null);
  assert.equal(sent, false);
  finish(); await reset;
  assert.equal(sent, true); assert.equal(reconnected, true);
  assert.equal(await client.command('heartbeat'), false);
  await client.dispose();
});

test('Hold cancels a declared action waiting for a fresh lease', async t => {
  t.mock.method(globalThis, 'setInterval', () => 0);
  const client = new ControlClient('fixture');
  client.session = 'private'; client.online = true; client.emit = () => {};
  let releaseLease, leaseRequested;
  const requested = new Promise(resolve => { leaseRequested = resolve; });
  const lease = new Promise(resolve => { releaseLease = resolve; });
  const operations = [];
  client.request = async (path, body) => {
    if (path === '/lease') { leaseRequested(); return lease; }
    operations.push(body.op); return {accepted: true};
  };
  const action = client.command('action', {action_id: 'position', parameters: {distance: 5}});
  await requested;
  const hold = client.command('hold');
  releaseLease({lease_id: 'lease', valid_for_ms: 500});
  assert.equal(await action, false);
  assert.deepEqual(await hold, {accepted: true});
  assert.deepEqual(operations, ['hold']);
  client.session = null;
  await client.dispose();
});
