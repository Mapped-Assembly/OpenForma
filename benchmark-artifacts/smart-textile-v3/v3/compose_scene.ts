/** Compose recorded poses through OpenIndustries' native portable scene API. */
import fs from 'node:fs';
import { Matrix4, Euler, Vector3 } from 'three';
import { finalizeAsset } from '../OpenIndustries/src/lib/scene';
import { makeManifest, readManifest, hydrateManifest } from '../OpenIndustries/src/lib/workspace';
const input=JSON.parse(fs.readFileSync('v3/oi-composition-input.json','utf8'));
const assets=input.parts.map(part=>finalizeAsset({id:part.id,name:part.name,source:{kind:'step',filename:'wearer-and-device-opencad.step',digest:input.source_sha256},parts:[part],hierarchy:{id:part.id+'-root',name:part.name,partIds:[part.id],children:[]},warnings:['CAD visual geometry driven by sampled simulation poses; no full collision or thermal validation.']}));
/** Restore asset-normalization offset in the recorded body coordinate frame. */
function pose(asset, frame) {
 const saved=frame.poses[asset.id],r=saved.rotation_matrix;
 const matrix=new Matrix4().set(r[0],r[1],r[2],0,r[3],r[4],r[5],0,r[6],r[7],r[8],0,0,0,0,1);
 const position=new Vector3(...asset.originOffset).applyMatrix4(matrix).add(new Vector3(...saved.position)).toArray();
 const angles=new Euler().setFromRotationMatrix(matrix,'XYZ');
 return {position,rotation:[angles.x,angles.y,angles.z]};
}
const items=assets.map(asset=>({id:asset.id,name:asset.name,asset,...pose(asset,input.frames[0]),visible:true}));
const tracks=assets.map(asset=>({id:asset.id+':instance',instanceId:asset.id,keys:input.frames.map((frame,i)=>({id:asset.id+'-'+i,time:frame.time,...pose(asset,frame),visible:true}))}));
const workspace={room:[4,4,3],items,animation:{duration:input.frames.at(-1).time,loop:false,tracks}};
const manifest=makeManifest(workspace,true);const parsed=readManifest(manifest);const hydrated=hydrateManifest(parsed,assets);
fs.writeFileSync('v3/smart-textile-v3.oi.json',JSON.stringify(manifest));
fs.writeFileSync('v3/oi-verification.json',JSON.stringify({status:'pass',instances:hydrated.items.length,tracks:tracks.length,keys:tracks.reduce((n,t)=>n+t.keys.length,0),source_trace_sha256:input.trace_sha256,scope:'Native OI composition, validation and hydration; review keyframes are sampled executed poses. OI may interpolate between keys; the GIF does not.'},null,2));
