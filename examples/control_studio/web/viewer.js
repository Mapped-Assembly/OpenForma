import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export class MachineViewer {
  constructor(element) {
    this.element = element; this.groups = new Map(); this.axes = new Map();
    this.renderer = new THREE.WebGLRenderer({antialias: true});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); this.renderer.setClearColor(0x162631);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.35;
    this.element.append(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.add(new THREE.HemisphereLight(0xd5e7ff, 0x455363, 2.3));
    for (const [color, intensity, pos] of [[0xffecd8, 3, [150,-200,280]], [0x94d1ff, 2, [-120,100,200]]]) {
      const light = new THREE.DirectionalLight(color, intensity); light.position.fromArray(pos); this.scene.add(light);
    }
    const grid = new THREE.GridHelper(500,25,0x45616f,0x29404d); grid.rotation.x = Math.PI/2; grid.position.z = -.5; this.scene.add(grid);
    this.camera = new THREE.PerspectiveCamera(35,1,.1,2000); this.camera.up.set(0,0,1);
    this.controls = new OrbitControls(this.camera,this.renderer.domElement); this.controls.enableDamping = true;
    this.controls.minDistance = 140; this.controls.maxDistance = 1000;
    this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(element);
    this.renderer.setAnimationLoop(() => { this.controls.update(); this.renderer.render(this.scene,this.camera); });
  }
  resize() { const w=this.element.clientWidth,h=this.element.clientHeight; this.renderer.setSize(w,h); this.camera.aspect=w/h; this.camera.updateProjectionMatrix(); }
  home() { this.camera.position.set(240,-340,300); this.controls.target.set(0,0,this.definition?.axes.some(a=>a.joint?.kind==='revolute')?60:30); this.controls.update(); }
  async load(definition) {
    if (this.model) {
      this.model.traverse(o => { o.geometry?.dispose(); o.material?.dispose(); }); this.scene.remove(this.model);
    }
    this.definition=definition; this.groups.clear(); this.axes.clear();
    const response=await fetch(`/assets/${definition.model_asset}`);
    if (!response.ok) throw new Error('CAD asset unavailable');
    const data=await response.json();
    this.model=new THREE.Group(); this.scene.add(this.model);
    const owners=new Map();
    for (const axis of definition.axes) {
      this.axes.set(axis.axis_id,axis);
      if (!axis.joint) continue;
      const group=new THREE.Group(); group.name=axis.axis_id;
      this.groups.set(axis.axis_id,group);
      for (const id of axis.joint.body_ids) { if (owners.has(id)) throw new Error('Duplicate CAD body binding'); owners.set(id,axis.axis_id); }
    }
    for (const axis of definition.axes) {
      if (!axis.joint) continue;
      const j=axis.joint, group=this.groups.get(axis.axis_id), parent=j.parent_axis?this.groups.get(j.parent_axis):this.model;
      const parentOrigin=j.parent_axis?this.axes.get(j.parent_axis).joint.origin_mm:[0,0,0];
      group.position.fromArray(j.origin_mm).sub(new THREE.Vector3(...parentOrigin));
      group.userData.rest=group.position.clone(); parent.add(group);
    }
    const seen=new Set(); this.bodies=new Map();
    for (const body of data.bodies) {
      const geometry=new THREE.BufferGeometry(); geometry.setAttribute('position',new THREE.Float32BufferAttribute(body.positions,3));
      geometry.setIndex(body.indices); geometry.computeVertexNormals();
      const color=new THREE.Color().setRGB(...body.color,THREE.SRGBColorSpace);
      const mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color,roughness:.6,metalness:.15})); mesh.name=body.id;
      const owner=owners.get(body.id);
      if (owner) { mesh.position.fromArray(this.axes.get(owner).joint.origin_mm).negate(); this.groups.get(owner).add(mesh); seen.add(body.id); }
      else this.model.add(mesh);
      this.bodies.set(body.id,mesh);
    }
    if (seen.size!==owners.size) throw new Error('CAD joint references missing geometry');
    this.bodyCount=data.bodies.length; this.home(); this.resize();
  }
  update(state) {
    for (const [id, group] of this.groups) {
      const value=state.execution?.kind==='native-firmware-sil'?state.axes[id]?.simulated:state.axes[id]?.commanded;
      if (value===null || value===undefined) continue;
      const j=this.axes.get(id).joint, axis=new THREE.Vector3(...j.axis);
      if (j.kind==='revolute') group.quaternion.setFromAxisAngle(axis,THREE.MathUtils.degToRad(j.sign*value));
      else group.position.copy(group.userData.rest).addScaledVector(axis,j.sign*value);
    }
    this.model.updateMatrixWorld(true);
  }
  bodyWorldPosition(id) { const mesh=this.bodies.get(id); if (!mesh) return null; return mesh.getWorldPosition(new THREE.Vector3()).toArray(); }
}
