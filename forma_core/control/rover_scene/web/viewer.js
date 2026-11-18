import * as THREE from 'three';
import {OrbitControls} from '/orbit.js';

const $ = id => document.getElementById(id);
const terminal = new Set(['completed','failed','cancelled','interrupted']);
let latest, spec, submitting = false;
const drafts = new Map();
let selected;

async function request(url, body) {
  const options = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const response = await fetch(url, options);
  const value = await response.json();
  if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value));
  return value;
}

async function meshGeometry(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Mesh download failed: ${response.status}`);
  const buffer = await response.arrayBuffer();
  const header = new DataView(buffer);
  const vertices = header.getUint32(0, true), faces = header.getUint32(4, true);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(buffer, 8, vertices * 3), 3));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(buffer, 8 + vertices * 12, faces * 3), 1));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return geometry;
}

function primitive(kind, size) {
  const [x, y, z] = size;
  switch (kind) {
    case 'box': return new THREE.BoxGeometry(2*x, 2*y, 2*z);
    case 'plane': return new THREE.PlaneGeometry(2*(x||8), 2*(y||8));
    case 'sphere': return new THREE.SphereGeometry(x, 16, 12);
    case 'ellipsoid': return new THREE.SphereGeometry(1, 16, 12).scale(x, y, z);
    case 'capsule': return new THREE.CapsuleGeometry(x, 2*y, 4, 12).rotateX(Math.PI/2);
    case 'cylinder': return new THREE.CylinderGeometry(x, x, 2*y, 16).rotateX(Math.PI/2);
    default: throw new Error(`Unsupported geometry: ${kind}`);
  }
}

function showState(state) {
  latest = state;
  const machines = state.machines;
  if (!selected) {
    for (const [key, machine] of Object.entries(machines)) $('machine').add(new Option(machine.name,key));
    selected = $('machine').value;
  }
  const task = machines[selected].task;
  const active = task && !terminal.has(task.status);
  $('goal').disabled = Boolean(active || submitting);
  $('start').disabled = Boolean(active || submitting);
  $('stop').disabled = !active || submitting;
  $('status').textContent = task ? [task.status,task.summary || task.reason || task.goal].filter(Boolean).join(' · ') : 'Idle';
  $('journal').hidden = !task;
  if (task) $('journal').href = '/api/tasks/'+encodeURIComponent(task.task_id);
  const events = (task?.events || []).filter(e => e.kind !== 'thinking').slice(-4);
  $('events').replaceChildren(...events.map(e => {
    const row=document.createElement('li'); row.textContent=e.message; return row;
  }));
  $('stock').replaceChildren(...Object.values(state.inventory).map(item => {
    const row=document.createElement('tr');
    for (const text of [item.label,spec.stations[item.station]?.label || 'In transit',machines[item.holder]?.name || '—']) {
      const cell=row.insertCell(); cell.textContent=text;
    }
    return row;
  }));
}

$('machine').onchange = () => {
  drafts.set(selected,$('goal').value);
  selected=$('machine').value;
  $('goal').value=drafts.get(selected) || '';
  $('error').textContent='';
  showState(latest);
};
async function command(url,body) {
  submitting=true; showState(latest); $('error').textContent='';
  try { await request(url,body); latest=await request('/api/state'); }
  catch(e) { $('error').textContent=e.message; }
  finally { submitting=false; showState(latest); }
}
$('task').onsubmit = e => {
  e.preventDefault();
  const goal=$('goal').value.trim();
  if (goal) command('/api/tasks',{machine_id:selected,goal,model:$('model').value.trim() || null});
};
$('stop').onclick = () => command('/api/machines/'+encodeURIComponent(selected)+'/stop',{});

async function startViewer() {
  spec = await request('/api/scene');
  document.title='Forma · '+spec.name; $('title').textContent=spec.name;
  const host=$('view'), nodes=new Map();
  const renderer=new THREE.WebGLRenderer({antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
  host.append(renderer.domElement);
  const scene=new THREE.Scene(); scene.background=new THREE.Color('#e4e8df');
  const camera=new THREE.PerspectiveCamera(38,1,.02,100);
  camera.up.set(0,0,1);
  const orbit=new OrbitControls(camera,renderer.domElement);
  const [[x0,y0],[x1,y1]]=spec.bounds;
  const span=Math.max(x1-x0,y1-y0), cx=(x0+x1)/2, cy=(y0+y1)/2;
  orbit.target.set(cx,cy,.15);
  camera.position.set(cx+span*.12,cy+span*.86,span*.64);
  orbit.maxPolarAngle=Math.PI*.48;
  scene.add(new THREE.HemisphereLight(0xffffff,0x6c8277,1.6));
  const sun=new THREE.DirectionalLight(0xfff2dc,1.65); sun.position.set(-3,2,7);scene.add(sun);
  const meshes=new Map(await Promise.all(Object.entries(spec.meshes).map(async ([id,url])=>[Number(id),await meshGeometry(url)])));
  for (const g of spec.geometries) {
    const shape=g.kind==='mesh'?meshes.get(g.mesh):primitive(g.kind,g.size);
    const node=new THREE.Mesh(shape,new THREE.MeshStandardMaterial({color:new THREE.Color(...g.rgba.slice(0,3)),roughness:.8}));
    node.position.fromArray(g.position);node.quaternion.fromArray(g.quaternion);
    nodes.set(g.id,node);scene.add(node);
  }
  const labels=Object.values(spec.stations).map(st => {
    const label=document.createElement('span');label.className='label';label.textContent=st.label;host.append(label);
    return [label,new THREE.Vector3(...st.point).add(new THREE.Vector3(0,.23,.10))];
  });
  new ResizeObserver(()=>{
    const w=host.clientWidth,h=host.clientHeight;renderer.setSize(w,h,false);
    camera.aspect=w/h;camera.fov=THREE.MathUtils.radToDeg(2*Math.atan(Math.tan(38*Math.PI/360)*Math.max(1,1.7/camera.aspect)));
    camera.updateProjectionMatrix();
  }).observe(host);
  function render() {
    orbit.update();renderer.render(scene,camera);
    for(const [label,point] of labels) {
      const p=point.clone().project(camera);
      label.style.left=(50+50*p.x)+'%';label.style.top=(50-50*p.y)+'%';
    }
    requestAnimationFrame(render);
  }
  render();
  async function poll() {
    try {
      const state=await request('/api/state');
      for(const [id,x,y,z,qx,qy,qz,qw] of state.poses) {
        nodes.get(id).position.set(x,y,z);nodes.get(id).quaternion.set(qx,qy,qz,qw);
      }
      showState(state);
    } catch(e) { $('status').textContent=e.message; }
    setTimeout(poll,100);
  }
  poll();
}
startViewer().catch(e => { $('error').textContent=e.message; });
