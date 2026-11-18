import test from 'node:test';
import assert from 'node:assert/strict';
import { ControlClient } from '../web/client.js';

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
