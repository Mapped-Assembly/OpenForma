import { ControlClient } from './client.js';
import { MachineViewer } from './viewer.js';
import { keyboardIntent, GamepadSource } from './inputs.js';
import { TaskPanel } from './tasks.js';

const $=id=>document.getElementById(id);
const viewer=new MachineViewer($('viewer'));
const held=new Set();
let machines=[],definition,client,state,gamepad,switching=false,pathEpoch=0,sliderTimer,pendingSlider=null;
let machinePackage=null;
const notify=text=>$('message').textContent=text;
const taskPanel=new TaskPanel({client:()=>client,machine:()=>definition?.machine_id,notify});
const active=()=>!switching && client?.online && client.session && ['ARMED','HOLD'].includes(state?.state);
const speed=()=>Number($('speed').value)/100;
const axisIds=()=>definition.axes.map(a=>a.axis_id);
const isRobot=()=>definition?.machine_id==='so101';
const toolPosition=()=>['x','y','z'].map(a=>Number($('tool-'+a).value));
function fillTool(position){position.forEach((v,i)=>$('tool-'+['x','y','z'][i]).value=Number(v.toFixed(1)));}
async function moveTool(position=toolPosition()){
  if(!active())return;
  cancelPath();clearSlider();const token=pathEpoch,own=client;
  try{
    const solution=await own.request('/ik',{target_mm:position});
    if(token!==pathEpoch||client!==own||!active())return;
    fillTool(position);await move(solution.targets,true);
  }catch(error){if(client===own)notify(error.message);}
}
function stepTool(index,delta){
  const position=state?.execution?.details?.tcp_mm?.slice();
  if(position){position[index]+=delta;moveTool(position);}
}
function cancelPath() { pathEpoch++; if ($('benchmark-status').textContent.startsWith('Running')) $('benchmark-status').textContent='Interrupted by manual input'; }
function clearSlider() { clearTimeout(sliderTimer); sliderTimer=null; pendingSlider=null; }
function hold() { cancelPath(); clearSlider(); held.clear(); if (active()) client.command('hold'); }
function disarm() { cancelPath(); clearSlider(); held.clear(); client?.command('disarm'); }
function jog(intent) {
  if (!active()) return;
  cancelPath(); clearSlider();
  const velocities=Object.fromEntries(definition.axes.filter(a=>intent[a.axis_id]!==undefined).map(a=>[a.axis_id,intent[a.axis_id]*a.max_rate*speed()]));
  client.command('jog',{velocities});
}
function move(targets,coordinated=false) {
  if (!active()) return Promise.resolve(false);
  for (const axis of definition.axes) if (axis.axis_id in targets) {
    $('target-'+axis.axis_id).value=targets[axis.axis_id];
    $('value-'+axis.axis_id).textContent=`${targets[axis.axis_id]} ${axis.unit==='degree'?'°':'mm'}`;
  }
  const rates=Object.fromEntries(definition.axes.filter(a=>a.axis_id in targets).map(a=>[a.axis_id,a.max_rate*speed()]));
  return client.command('move',{targets,rates,coordinated});
}
function renderControls() {
  $('machine-title').textContent=definition.name; $('description').textContent=definition.description;
  document.body.classList.toggle('robot-mode',isRobot());$('tool-controls').hidden=!isRobot();$('task-panel').hidden=!isRobot();
  $('joint-panel').open=!isRobot();
  $('input-hint').textContent=isRobot()?'Arrows: tool X/Y steps · joystick: shoulder pan/lift':definition.axes.length===1?'↑ → Open · ↓ ← Close · release to Hold':'← → X · ↑ ↓ Y · release to Hold';
  document.querySelector('#dpad span').textContent=isRobot()?'TAP TO STEP':'HOLD TO JOG';
  $('axis-controls').replaceChildren(); $('readouts').replaceChildren();
  for (const axis of definition.axes) {
    const control=document.createElement('section'); control.className='axis-control';
    const row=document.createElement('div'),label=document.createElement('label'),value=document.createElement('output');
    label.textContent=axis.label; label.htmlFor='target-'+axis.axis_id;
    value.id='value-'+axis.axis_id; value.textContent=`${axis.neutral} ${axis.unit==='degree'?'°':'mm'}`;
    row.append(label,value);
    const input=document.createElement('input'); Object.assign(input,{type:'range',min:axis.minimum,max:axis.maximum,step:.5,value:axis.neutral,id:'target-'+axis.axis_id}); input.setAttribute('aria-label',axis.label+' target');
    input.addEventListener('input',()=>{
      value.textContent=`${input.value} ${axis.unit==='degree'?'°':'mm'}`;
      if (!active()) return;
      cancelPath(); pendingSlider={axis:axis.axis_id,value:Number(input.value)};
      if (!sliderTimer) sliderTimer=setTimeout(()=>{const next=pendingSlider; sliderTimer=null; pendingSlider=null; if(next) move({[next.axis]:next.value});},60);
    });
    const limits=document.createElement('small'); const lo=document.createElement('span'),hi=document.createElement('span');
    lo.textContent=`${Number(axis.minimum.toFixed(1))} ${axis.unit}`; hi.textContent=`${Number(axis.maximum.toFixed(1))} ${axis.unit}`; limits.append(lo,hi);
    control.append(row,input,limits); $('axis-controls').append(control);
    const readout=document.createElement('div'); readout.className='readout';
    const rl=document.createElement('label'); rl.textContent=`${definition.backend==='virtual'?'SIMULATED':'MEASURED'} · ${axis.label.toUpperCase()}`;
    const position=document.createElement('strong'); position.id='position-'+axis.axis_id; position.textContent='—';
    const target=document.createElement('small'); target.id='readout-target-'+axis.axis_id; target.textContent='Output disabled';
    readout.append(rl,position,target); $('readouts').append(readout);
  }
  $('joint-count').textContent=`${definition.axes.length} ${definition.axes.length>1?'AXES':'AXIS'} · ${isRobot()?'MUJOCO BODY HIERARCHY':'NESTED CAD BINDINGS'}`;
  $('benchmark').parentElement.hidden=definition.axes.length!==2;
  $('together').textContent=definition.axes.length>1?'Move together to targets ↗':'Move to target ↗';
  gamepad=new GamepadSource(axisIds(),jog,text=>$('gamepad-status').textContent=text);
}
function renderActions(){
  $('action-panel').hidden=!machinePackage;$('declared-actions').replaceChildren();
  if(!machinePackage)return;
  $('action-binding').textContent=`${machinePackage.adapter.adapter_id} · ${machinePackage.adapter.evidence}`;
  for(const action of machinePackage.actions){
    const section=document.createElement('section');section.className='declared-action';
    const inputs={};
    for(const [name,param] of Object.entries(action.parameters)){
      const label=document.createElement('label');label.textContent=`${name} (${param.unit})`;
      const input=document.createElement('input');Object.assign(input,{type:'number',min:param.minimum,max:param.maximum,step:'any',value:Math.max(param.minimum,Math.min(0,param.maximum))});
      input.setAttribute('aria-label',`${action.action_id} ${name}`);inputs[name]=input;label.append(input);section.append(label);
    }
    const button=document.createElement('button');button.textContent=action.label;button.className='full';button.dataset.action=action.action_id;
    button.onclick=()=>{if(!active())return;cancelPath();clearSlider();held.clear();client.command('action',{action_id:action.action_id,parameters:Object.fromEntries(Object.entries(inputs).map(([k,v])=>[k,Number(v.value)]))});};
    const note=document.createElement('small');note.textContent=`Completion: ${action.completion.source} feedback · ${action.completion.timeout_s}s timeout`;
    section.append(button,note);$('declared-actions').append(section);
  }
}
function renderLiveExecution(){
  const connected=client?.online&&!switching&&state?.machine_id===definition?.machine_id;
  const action=state?.action,owner=state?.owner;
  $('live-execution').dataset.status=!connected?'offline':action?.status==='running'?'running':'idle';
  $('live-stream').textContent=connected?'LIVE · streaming machine feedback':'OFFLINE · last received pose';
  $('live-controller').textContent=owner?`${owner.kind==='agent'?'Agent':'Controller'}: ${owner.name}`:'No controller · output disabled';
  if(!connected){
    $('live-action').textContent='Waiting for live machine feedback';
    $('live-detail').textContent='The displayed pose is not updating until the stream reconnects.';
    return;
  }
  const label=machinePackage?.actions.find(a=>a.action_id===action?.action_id)?.label||action?.action_id;
  if(action){
    $('live-action').textContent=`${action.status==='running'?'Executing':'Last action'}: ${label} · ${action.status}`;
    const parameters=Object.entries(action.parameters).map(([key,value])=>`${key}=${Number(value.toFixed(2))}`).join(' · ');
    $('live-detail').textContent=[parameters,`${action.provenance} / ${action.feedback_source}`,action.reason,
      state.state==='DISARMED'?'Paused · final pose retained':action.status!=='running'&&owner?.kind==='agent'?'Waiting for the agent’s next action':null].filter(Boolean).join(' · ');
  }else{
    $('live-action').textContent=state.state==='DISARMED'?'Paused · machine disarmed':owner?.kind==='agent'?'Agent connected · waiting for an action':'Manual control · '+state.state;
    $('live-detail').textContent='Robot and object poses below follow execution feedback from the server.';
  }
}
function update(data) {
  state=data.telemetry; viewer.update(state);
  if('task' in data)taskPanel.update(data.task);else taskPanel.render();
  renderLiveExecution();
  document.querySelectorAll('#declared-actions button,#declared-actions input').forEach(e=>e.disabled=!active());
  const result=state.action;
  $('action-result').textContent=result?`Last action: ${result.action_id} · ${result.status} · ${result.provenance} / ${result.feedback_source}${result.reason?' · '+result.reason:''}`:'No declared action attempted.';
  const task=state.execution?.kind==='mujoco-manipulation'?state.execution.details:null;
  if(task){
    const xyz=p=>p.map(v=>v.toFixed(1)).join(', ');
    $('task-state').textContent=task.success?'SUCCESS · placed and released':task.lifted?'LIFT VERIFIED':task.contact_sides.length===2?'BOTH JAWS IN CONTACT':'READY TO GRASP';
    $('task-observation').textContent=`Tool [${xyz(task.tcp_mm)}] mm · Cube [${xyz(task.cube_mm)}] mm · Target [${xyz(task.target_mm)}] mm`;
    $('task-contact').textContent=`Jaw contacts: ${task.contact_sides.join(' + ')||'none'} · maximum lift ${task.max_lift_mm.toFixed(1)} mm · settled ${task.settled_s.toFixed(2)} s · simulation ${task.time_s.toFixed(1)} s`;
    $('task-reset').disabled=!client.online||!client.session||switching;
    document.querySelectorAll('#tool-controls button,#tool-controls input').forEach(e=>e.disabled=!active());
  }
  const firmware=state.execution?.kind==='native-firmware-sil'?state.execution:null;
  $('firmware-panel').hidden=!firmware;
  if(firmware){
    const d=firmware.details;
    $('firmware-state').textContent=state.execution_fault||firmware.status;
    $('firmware-summary').textContent=`Firmware link ${d.link_connected?'connected':'disconnected'} · watchdog ${d.watchdog_ms} ms · ${d.accepted} accepted / ${d.rejected} rejected frames`;
    $('firmware-assumptions').textContent=d.assumptions;
    $('firmware-axes').replaceChildren(...definition.axes.map(a=>{
      const v=d.axes[a.axis_id],row=document.createElement('p');
      row.textContent=`${a.label}: ${v.drive==='servo'?`${v.pulse_us} µs PWM / ${v.pwm_period_us} µs period`:`${v.signed_steps} signed steps · ${v.pulses} pulses · DIR ${v.direction}`} · firmware ${v.firmware_position.toFixed(2)} · plant ${v.simulated.toFixed(2)} ${a.unit} ${v.stalled?'· STALLED':''}`;
      return row;
    }));
    $('firmware-link').textContent=d.link_connected?'Drop firmware link':'Reconnect firmware link';
    $('firmware-stall').textContent=d.axes[definition.axes[0].axis_id].stalled?'Release simulated actuator':'Stall simulated actuator';
    for(const id of ['firmware-link','firmware-stall','firmware-reset'])$(id).disabled=!client.online||!client.session||switching;
  }
  $('state').textContent=state.state; $('state').className=state.state;
  for (const a of definition.axes) {
    const s=state.axes[a.axis_id],unit=a.unit==='degree'?'°':' mm', observed=state.backend==='virtual'?s.simulated:s.measured;
    $('position-'+a.axis_id).textContent=observed===null?'—':observed.toFixed(1)+unit;
    $('readout-target-'+a.axis_id).textContent=s.target===null?'Output disabled':`Target ${s.target.toFixed(1)}${unit} · ${state.command_motion_complete?'command complete':'moving'}`;
  }
  $('pose-note').textContent=firmware?(firmware.output_enabled?'FIRMWARE OUTPUT → SIMULATED PLANT':'Last simulated / reference pose · firmware output disabled'):(state.output_enabled?'SIMULATED · authoritative runtime state':'Reference / last command pose · output disabled');
  if(task)$('pose-note').textContent=`MUJOCO CONTACT PHYSICS · ${state.output_enabled?'RUNNING':'PAUSED'} · SIMULATED FEEDBACK`;
  const usable=client.online && !!client.session && !switching;
  $('arm').disabled=!usable || ['ARMED','FAULT'].includes(state.state);
  $('disarm').disabled=!usable || ['DISARMED','FAULT'].includes(state.state);
  $('release').disabled=!client.online || !client.session;
  $('claim').disabled=!client.online || !!client.session || switching;
  $('claim').textContent=client.session?'You have control':state.owner?'Take over control':'Take control';
  for(const id of ['hold','together','benchmark']) $(id).disabled=!active();
  document.querySelectorAll('#dpad button,#axis-controls input').forEach(e=>e.disabled=!active());
  if(['LINK_LOST','FAULT'].includes(state.state)){cancelPath();clearSlider();held.clear();}
  $('events').replaceChildren(...data.events.slice(-5).reverse().map(event=>{
    const li=document.createElement('li'),time=document.createElement('time'),text=document.createElement('span');
    time.textContent=(event.uptime_ms/1000).toFixed(1)+'s';
    const target=event.targets?Object.entries(event.targets).filter(([,v])=>v!==null).map(([k,v])=>`${k}=${Number(v).toFixed(1)}`).join(' · '):'';
    text.textContent=event.message+(target?' → '+target:''); li.append(time,text);return li;
  }));
}
async function selectMachine(id) {
  if(switching)return;
  switching=true; $('machine').disabled=true; cancelPath(); clearSlider(); held.clear();
  state=null;renderLiveExecution();
  try {
    if(client)await client.dispose();
    definition=machines.find(m=>m.machine_id===id); state=null;machinePackage=null;renderActions();taskPanel.select(null,null);
    renderControls(); $('loading').style.display='grid'; $('loading').textContent='Loading native CAD…';
    await viewer.load(definition); $('body-count').textContent=`${isRobot()?'SO-101 MODEL':'NATIVE CAD'} · ${viewer.bodyCount} BODIES`; $('loading').style.display='none';
    const current=client=new ControlClient(id);
    current.addEventListener('connection',e=>{if(client!==current)return; $('connection').textContent=e.detail?'Runtime connected':'Runtime disconnected'; $('connection').classList.toggle('online',e.detail);renderLiveExecution();taskPanel.render(); if(!e.detail){held.clear();cancelPath();}});
    current.addEventListener('state',e=>{if(client===current)update(e.detail);});
    current.addEventListener('notice',e=>{if(client===current)notify(e.detail);});
    const initial=await current.request('/state');
    const capabilities=await current.request('/capabilities');
    machinePackage=capabilities.machine_package;renderActions();
    taskPanel.select(machinePackage,initial.task,capabilities);
    current.connect();
    if(!initial.telemetry.owner)await current.claim();
    notify(isRobot()?'Arm to start. Fill above cube, then Move tool to XYZ. The task card shows the manual sequence.':'Press A to arm. Hold arrow keys to jog; release to hold.');
  } catch(error) { notify(error.message); $('loading').textContent=error.message; }
  finally { switching=false; $('machine').disabled=false; }
}
$('machine').onchange=()=>selectMachine($('machine').value);
$('home').onclick=()=>viewer.home();
$('claim').onclick=()=>client.claim(!!state.owner).catch(e=>notify(e.message));
$('release').onclick=()=>{cancelPath();clearSlider();held.clear();client.release().catch(e=>notify(e.message));};
$('arm').onclick=()=>client.command('arm'); $('hold').onclick=hold; $('disarm').onclick=disarm;
$('speed').oninput=()=>{$('speed-value').value=$('speed').value+'%';if(held.size)jog(keyboardIntent(held,axisIds()));else if(gamepad?.moving)jog(gamepad.last);};
$('together').onclick=()=>{cancelPath();clearSlider();move(Object.fromEntries(definition.axes.map(a=>[a.axis_id,Number($('target-'+a.axis_id).value)])),true);};
$('source').onchange=()=>{hold();$('gamepad-status').hidden=$('source').value!=='gamepad';};
async function simulation(op,axis){
  cancelPath();clearSlider();held.clear();
  try{if(op==='reset'){
      await client.resetSimulation();renderControls();if(isRobot())fillTool([220,25,70]);
      notify('Simulation reset to its startup fixture. Take control, then Arm.');
    }else await client.request('/simulation',{session_id:client.session,op,...(axis?{axis}:{})});
  }catch(error){notify(error.message);}
}
$('firmware-link').onclick=()=>simulation(state.execution.details.link_connected?'disconnect':'reconnect');
$('firmware-stall').onclick=()=>{const axis=definition.axes[0].axis_id;simulation(state.execution.details.axes[axis].stalled?'unstall':'stall',axis);};
$('firmware-reset').onclick=()=>simulation('reset');
$('task-reset').onclick=()=>simulation('reset');
$('tool-move').onclick=()=>moveTool();
$('source-preset').onclick=()=>{const p=state.execution.details.cube_mm;fillTool([p[0],p[1],70]);};
$('target-preset').onclick=()=>{const p=state.execution.details.target_mm;fillTool([p[0],p[1],70]);};
$('tool-up').onclick=()=>stepTool(2,5);$('tool-down').onclick=()=>stepTool(2,-5);
function gripper(value){cancelPath();clearSlider();move({gripper:value});}
$('gripper-open').onclick=()=>gripper(85);$('gripper-close').onclick=()=>gripper(0);

window.addEventListener('keydown',event=>{
  if(event.code==='Escape'){event.preventDefault();disarm();return;}
  if(['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)||event.target.isContentEditable||event.ctrlKey||event.altKey||event.metaKey)return;
  if(isRobot()&&$('source').value==='keyboard'&&['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','PageUp','PageDown','KeyO','KeyC'].includes(event.code)){
    event.preventDefault();if(event.repeat)return;
    if(event.code==='KeyO'||event.code==='KeyC')gripper(event.code==='KeyO'?85:0);
    else {const [index,delta]={ArrowRight:[0,5],ArrowLeft:[0,-5],ArrowUp:[1,5],ArrowDown:[1,-5],PageUp:[2,5],PageDown:[2,-5]}[event.code];stepTool(index,delta);}
    return;
  }
  if(!['KeyA','Space','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.code))return;
  event.preventDefault();if(event.repeat)return;
  if(event.code==='KeyA')client?.command('arm');
  else if(event.code==='Space')hold();
  else if($('source').value==='keyboard' && definition){held.add(event.code);jog(keyboardIntent(held,axisIds()));}
});
window.addEventListener('keyup',event=>{if(held.delete(event.code)){event.preventDefault();jog(keyboardIntent(held,axisIds()));}});
window.addEventListener('blur',hold);
document.addEventListener('visibilitychange',()=>{if(document.hidden)hold();});
for(const button of document.querySelectorAll('#dpad button')) {
  let down=false;
  button.onpointerdown=event=>{if(button.disabled)return;event.preventDefault();button.setPointerCapture(event.pointerId);down=true;
    if(isRobot()){down=false;const [index,delta]={up:[1,5],down:[1,-5],left:[0,-5],right:[0,5]}[button.dataset.dir];stepTool(index,delta);return;}
    const key={up:'ArrowUp',down:'ArrowDown',left:'ArrowLeft',right:'ArrowRight'}[button.dataset.dir];held.add(key);jog(keyboardIntent(held,axisIds()));};
  const stop=()=>{if(down){down=false;hold();}};
  button.onpointerup=stop;button.onpointercancel=stop;button.onlostpointercapture=stop;
}
setInterval(()=>{
  if(!gamepad)return;
  const pads=navigator.getGamepads?.()||[], pad=Array.from(pads).find(p=>p?.connected);
  gamepad.sample(pad,$('source').value==='gamepad' && active() && !document.hidden && document.hasFocus());
},50);

$('benchmark').onclick=async()=>{
  if(!active()||definition.axes.length!==2)return;
  hold();const token=++pathEpoch,own=client;
  const [a,b]=definition.axes,loA=a.minimum*.75,hiA=a.maximum*.75,loB=b.minimum*2/3,hiB=b.maximum*2/3;
  const points=[[loA,loB],[hiA,loB],[hiA,hiB],[loA,hiB],[loA,loB],[a.neutral,b.neutral]];
  try {
    for(let i=0;i<points.length;i++){
      if(token!==pathEpoch||client!==own||!active())return;
      $('benchmark-status').textContent=`Running ${i+1} / ${points.length}`;
      const targets={[a.axis_id]:points[i][0],[b.axis_id]:points[i][1]};
      const reply=await move(targets,true);if(!reply)throw new Error('Path command rejected');
      const deadline=performance.now()+20000;
      while(token===pathEpoch && active() && client===own){
        const source=state.execution?.kind==='native-firmware-sil'?'simulated':'commanded';
        if(state.ack_seq>=reply.ack_seq && Object.entries(targets).every(([id,t])=>state.axes[id][source]!==null&&Math.abs(state.axes[id][source]-t)<.02))break;
        if(performance.now()>deadline)throw new Error('Path segment timed out');
        await new Promise(resolve=>setTimeout(resolve,30));
      }
    }
    if(token===pathEpoch){await own.command('hold');$('benchmark-status').textContent='Path complete · 6 coordinated moves';notify('Benchmark path completed in simulated coordinates.');}
  }catch(error){if(client===own)hold();notify(error.message);}
};

window.controlStudio=Object.freeze({get telemetry(){return state?structuredClone(state):null;},get machine(){return definition?.machine_id;},
  get bodyCount(){return viewer.bodyCount;},bodyPosition:id=>viewer.bodyWorldPosition(id)});
async function start(){
  await taskPanel.discover();
  for(const id of ['arm','disarm','hold','together','benchmark','claim','release'])$(id).disabled=true;
  const response=await fetch('/api/control/v2/machines');machines=await response.json();
  for(const machine of machines){const option=document.createElement('option');option.value=machine.machine_id;option.textContent=machine.name;$('machine').append(option);}
  $('machine').value=machines.some(m=>m.machine_id==='so101')?'so101':machines.some(m=>m.machine_id==='servo-gate-sil')?'servo-gate-sil':machines.some(m=>m.machine_id==='xy-stage')?'xy-stage':machines[0].machine_id;
  await selectMachine($('machine').value);
}
start().catch(e=>notify('Unable to initialize: '+e.message));
