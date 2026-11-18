import { ControlClient } from './client.js';
import { MachineViewer } from './viewer.js';
import { keyboardIntent, GamepadSource } from './inputs.js';

const $=id=>document.getElementById(id);
const viewer=new MachineViewer($('viewer'));
const held=new Set();
let machines=[],definition,client,state,gamepad,switching=false,pathEpoch=0,sliderTimer,pendingSlider=null;
const notify=text=>$('message').textContent=text;
const active=()=>!switching && client?.online && client.session && ['ARMED','HOLD'].includes(state?.state);
const speed=()=>Number($('speed').value)/100;
const axisIds=()=>definition.axes.map(a=>a.axis_id);
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
  $('input-hint').textContent=definition.axes.length===1?'↑ → Open · ↓ ← Close · release to Hold':'← → X · ↑ ↓ Y · release to Hold';
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
    lo.textContent=`${axis.minimum} ${axis.unit}`; hi.textContent=`${axis.maximum} ${axis.unit}`; limits.append(lo,hi);
    control.append(row,input,limits); $('axis-controls').append(control);
    const readout=document.createElement('div'); readout.className='readout';
    const rl=document.createElement('label'); rl.textContent=`${definition.backend==='virtual'?'SIMULATED':'MEASURED'} · ${axis.label.toUpperCase()}`;
    const position=document.createElement('strong'); position.id='position-'+axis.axis_id; position.textContent='—';
    const target=document.createElement('small'); target.id='readout-target-'+axis.axis_id; target.textContent='Output disabled';
    readout.append(rl,position,target); $('readouts').append(readout);
  }
  $('joint-count').textContent=`${definition.axes.length} ${definition.axes.length>1?'AXES':'AXIS'} · NESTED CAD BINDINGS`;
  $('benchmark').parentElement.hidden=definition.axes.length!==2;
  $('together').textContent=definition.axes.length>1?'Move together to targets ↗':'Move to target ↗';
  gamepad=new GamepadSource(axisIds(),jog,text=>$('gamepad-status').textContent=text);
}
function update(data) {
  state=data.telemetry; viewer.update(state);
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
  try {
    if(client)await client.dispose();
    definition=machines.find(m=>m.machine_id===id); state=null;
    renderControls(); $('loading').style.display='grid'; $('loading').textContent='Loading native CAD…';
    await viewer.load(definition); $('body-count').textContent=`NATIVE CAD · ${viewer.bodyCount} BODIES`; $('loading').style.display='none';
    const current=client=new ControlClient(id);
    current.addEventListener('connection',e=>{if(client!==current)return; $('connection').textContent=e.detail?'Runtime connected':'Runtime disconnected'; $('connection').classList.toggle('online',e.detail); if(!e.detail){held.clear();cancelPath();}});
    current.addEventListener('state',e=>{if(client===current)update(e.detail);});
    current.addEventListener('notice',e=>{if(client===current)notify(e.detail);});
    const initial=await current.request('/state');
    current.connect();
    if(!initial.telemetry.owner)await current.claim();
    notify('Press A to arm. Hold arrow keys to jog; release to hold.');
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
  try{await client.request('/simulation',{session_id:client.session,op,...(axis?{axis}:{})});
    if(op==='reset')notify('Simulation reset to its startup fixture. Take control, then Arm.');
  }catch(error){notify(error.message);}
}
$('firmware-link').onclick=()=>simulation(state.execution.details.link_connected?'disconnect':'reconnect');
$('firmware-stall').onclick=()=>{const axis=definition.axes[0].axis_id;simulation(state.execution.details.axes[axis].stalled?'unstall':'stall',axis);};
$('firmware-reset').onclick=()=>simulation('reset');

window.addEventListener('keydown',event=>{
  if(event.code==='Escape'){event.preventDefault();disarm();return;}
  if(['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)||event.target.isContentEditable||event.ctrlKey||event.altKey||event.metaKey)return;
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
  for(const id of ['arm','disarm','hold','together','benchmark','claim','release'])$(id).disabled=true;
  const response=await fetch('/api/control/v2/machines');machines=await response.json();
  for(const machine of machines){const option=document.createElement('option');option.value=machine.machine_id;option.textContent=machine.name;$('machine').append(option);}
  $('machine').value=machines.some(m=>m.machine_id==='servo-gate-sil')?'servo-gate-sil':machines.some(m=>m.machine_id==='xy-stage')?'xy-stage':machines[0].machine_id;
  await selectMachine($('machine').value);
}
start().catch(e=>notify('Unable to initialize: '+e.message));
