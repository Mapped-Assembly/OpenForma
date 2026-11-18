/** Browser task controls; the server owns execution across reloads/disconnects. */
export const taskActive = task => !!task && ['starting', 'running'].includes(task.status);

export class TaskPanel {
  constructor({client, machine, notify}) {
    this.client = client; this.machine = machine; this.notify = notify;
    this.task = null; this.pending = false; this.available = false; this.package = null;
    this.$ = id => document.getElementById(id);
    this.$('task-run').onclick = () => this.start();
    this.$('task-stop').onclick = () => this.stop();
    this.$('task-takeover').onclick = async () => {
      try { await this.client().claim(true); } catch(error) { notify(error.message); }
    };
  }
  async discover() {
    try {
      const response = await fetch('/api/control/v2/task-runner');
      if (!response.ok) throw Error('Task runner unavailable');
      const runner = await response.json();
      this.available = runner.available;
      this.$('task-runner-note').textContent = runner.available ? 'OpenCode SDK · host-managed heartbeat and evidence' : runner.message;
    } catch(error) { this.$('task-runner-note').textContent = error.message; }
    this.render();
  }
  select(packageValue, task, {simulation_reset = false} = {}) {
    this.package = packageValue; this.task = task; this.key = null;
    this.$('task-goal-panel').hidden = !packageValue;
    this.$('task-fresh-label').hidden = !simulation_reset;
    this.$('task-fresh').checked = false;
    this.$('task-goal').value = taskActive(task) ? task.goal : '';
    this.render();
  }
  update(task) { this.task = task; this.render(); }
  render() {
    const active = taskActive(this.task), online = this.client()?.online;
    this.$('task-run').disabled = this.pending || active || !online || !this.available || !this.package;
    this.$('task-stop').disabled = !active || !online;
    this.$('task-takeover').disabled = !active || !online;
    for (const id of ['task-goal','task-model','task-fresh']) this.$(id).disabled = this.pending || active;
    const key = this.task ? `${this.task.task_id}:${this.task.revision}` : 'none';
    if (key === this.key) return;
    this.key = key;
    this.$('task-job-status').textContent = this.task?.status.toUpperCase() || 'READY';
    this.$('task-job-summary').textContent = this.task?.summary || this.task?.reason || (active ? this.task.goal : 'Enter a goal. The agent will discover actions and verify results from feedback.');
    const link = this.$('task-evidence'); link.hidden = !this.task;
    if (this.task) link.href = `/api/control/v2/machines/${encodeURIComponent(this.machine())}/tasks/${this.task.task_id}`;
    const model = this.task?.model;
    this.$('task-used-model').textContent = model ? `Model: ${typeof model === 'string' ? model : `${model.providerID}/${model.id}`}` : '';
    this.$('task-timeline').replaceChildren(...(this.task?.events || []).slice(-20).map(event => {
      const item = document.createElement('li');
      const time = document.createElement('time'); time.textContent = new Date(event.time).toLocaleTimeString();
      const text = document.createElement('span'); text.textContent = event.message;
      item.append(time, text); return item;
    }));
    this.$('task-timeline').scrollTop = this.$('task-timeline').scrollHeight;
  }
  async start() {
    const goal = this.$('task-goal').value.trim();
    if (!goal) { this.notify('Enter a machine goal first.'); return; }
    this.pending = true; this.render();
    const client = this.client();
    try {
      const task = await client.startTask(goal, {reset:this.$('task-fresh').checked, model:this.$('task-model').value.trim() || null});
      if (this.client() === client) { this.update(task); this.notify('Task started. Watch the live robot and task timeline.'); }
    } catch(error) { this.notify(error.message); }
    finally { this.pending = false; this.render(); }
  }
  async stop() {
    const client = this.client();
    try {
      const task = await client.request('/tasks/stop', {});
      if (this.client() === client) this.update(task);
    } catch(error) { this.notify(error.message); }
  }
}
