export class ControlClient extends EventTarget {
  constructor(machineId) {
    super(); this.id = machineId; this.base = `/api/control/v2/machines/${machineId}`;
    this.session = null; this.ownerId = null; this.seq = 0; this.lease = null; this.online = false; this.epoch = 0;
    this.queue = Promise.resolve(); this.closed = false; this.heartbeatPending = false;
    this.timer = setInterval(() => {
      if (this.session && this.online && this.state?.state !== 'FAULT' && !document.hidden && !this.heartbeatPending) {
        this.heartbeatPending = true; this.command('heartbeat').finally(() => this.heartbeatPending = false);
      }
      if (this.online && performance.now() - this.lastFrame > 1200) { this.online = false; this.emit('connection', false); }
    }, 200);
  }
  emit(name, data) { this.dispatchEvent(new CustomEvent(name, {detail: data})); }
  async request(path, body) {
    const response = await fetch(this.base + path, {method: body === undefined ? 'GET' : 'POST',
      headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(2500)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.reason || `HTTP ${response.status}`);
    return data;
  }
  connect() {
    if (this.closed) return;
    clearTimeout(this.retry);
    if (this.ws) { this.ws.onclose = null; this.ws.close(); }
    const url = new URL(this.base + '/stream', location.href); url.protocol = 'ws:';
    if (this.session) url.searchParams.set('session_id', this.session);
    const ws = this.ws = new WebSocket(url);
    ws.onmessage = event => {
      if (ws !== this.ws || this.closed) return;
      const data = JSON.parse(event.data); this.lastFrame = performance.now();
      if (!this.online) { this.online = true; this.emit('connection', true); }
      if (this.session && data.telemetry.owner?.owner_id !== this.ownerId) { this.session = null; this.ownerId = null; this.lease = null; this.epoch++; this.emit('notice', 'Control ownership changed.'); }
      if (data.lease) this.lease = {...data.lease, received: performance.now()};
      this.state = data.telemetry; this.emit('state', data);
    };
    ws.onclose = () => { if (ws !== this.ws) return; this.online = false; this.epoch++; this.emit('connection', false); if (!this.closed) this.retry = setTimeout(() => this.connect(), 1000); };
    ws.onerror = () => ws.close();
  }
  async claim(takeover = false) {
    const response = await this.request('/claim', {name: 'Control Studio', kind: 'human', takeover});
    this.session = response.session_id; this.ownerId = response.owner_id; this.lease = {...response, received: performance.now()}; this.seq = 0; this.epoch++; this.connect();
  }
  command(op, params = {}) {
    if (['hold', 'disarm'].includes(op)) this.epoch++;
    const epoch = this.epoch, session = this.session;
    const task = async () => {
      if (!session || session !== this.session || !this.online || this.closed) return false;
      if (op === 'heartbeat' && this.state?.state === 'FAULT') return false;
      if (['jog', 'move', 'action'].includes(op) && epoch !== this.epoch) return false;
      try {
        if (!this.lease || performance.now() - this.lease.received > this.lease.valid_for_ms - 80) this.lease = {...await this.request('/lease', {session_id: session}), received: performance.now()};
        if (session !== this.session || (['jog', 'move', 'action'].includes(op) && epoch !== this.epoch)) return false;
        const response = await this.request('/command', {protocol_version: 2, session_id: session, lease_id: this.lease.lease_id, seq: this.seq++, op, ...params});
        if (op !== 'heartbeat') this.emit('accepted', response);
        return response;
      } catch (error) { this.emit('notice', error.message); return false; }
    };
    const result = this.queue.then(task, task); this.queue = result; return result;
  }
  async release() {
    const session = this.session; this.session = null; this.ownerId = null; this.lease = null; this.epoch++;
    await this.queue;
    if (session) await this.request('/release', {session_id: session});
    if (!this.closed) this.connect();
  }
  async resetSimulation() {
    const session = this.session;
    if (!session) return false;
    // Stop issuing heartbeats and invalidate queued moves before reset revokes
    // the server session. Drain an in-flight command first.
    this.session = null; this.ownerId = null; this.lease = null; this.epoch++;
    await this.queue;
    try { return await this.request('/simulation', {session_id: session, op: 'reset'}); }
    finally { if (!this.closed) this.connect(); }
  }
  async startTask(goal, options = {}) {
    // Atomically hand the current browser session to the task host. Stop local
    // heartbeats and drain already-dispatched commands before the handoff.
    const session = this.session, owner = this.ownerId;
    this.session = null; this.ownerId = null; this.lease = null; this.epoch++;
    await this.queue;
    try { return await this.request('/tasks', {goal, ...options, session_id: session}); }
    catch (error) {
      // A definite rejected start did not transfer ownership. An ambiguous
      // transport failure is reconciled against public state before restoring it.
      try {
        const {telemetry} = await this.request('/state');
        if (owner && telemetry.owner?.owner_id === owner) { this.session = session; this.ownerId = owner; }
      } catch {}
      throw error;
    } finally { if (!this.closed) this.connect(); }
  }
  async dispose() {
    this.closed = true; clearInterval(this.timer); clearTimeout(this.retry);
    if (this.ws) { this.ws.onclose = null; this.ws.close(); }
    await this.release();
  }
}
