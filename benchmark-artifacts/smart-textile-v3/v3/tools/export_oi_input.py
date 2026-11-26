"""Export native CAD visuals with sampled executed body poses for OI composition."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import mujoco
import numpy as np
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from forma_core.control.mujoco_adapter import MujocoConfiguration, MujocoAdapter
from opencad.runtime import RuntimeContext
from opencad.kernel.core.occt_backend import OcctBackend, _tessellate_shape


def export(configuration: Path, trace_path: Path, geometry: Path, cad: Path, output: Path, layer_geometry: Path | None = None) -> None:
    """Read verified simulation states and native geometry; never integrate motion."""
    config=MujocoConfiguration.model_validate_json(configuration.read_text())
    adapter=MujocoAdapter(config,configuration.parent,clock=lambda:0.)
    trace=json.loads(trace_path.read_text())
    if hashlib.sha256(configuration.read_bytes()).hexdigest()!=trace['configuration_sha256']:
        raise ValueError('Trace/configuration mismatch')
    backend=OcctBackend();context=RuntimeContext(backend=backend)
    _,shape_id=context.execute_operation('import_step',{'filepath':str(cad.resolve())},feature_name='OI source')
    explorer=TopExp_Explorer(backend.get_native_shape(shape_id),TopAbs_SOLID);solids=[]
    while explorer.More():solids.append(explorer.Current());explorer.Next()
    objects=json.loads(geometry.read_text())['objects']
    if len(solids)!=len(objects):raise ValueError('CAD/identity count mismatch')
    conversion=np.array([[1.,0,0],[0,0,1],[0,-1,0]])
    parts=[];ids={}
    for obj,solid in zip(objects,solids,strict=True):
        name=obj['id'];bid=adapter.model.body(name).id;ids[name]=bid
        mesh=_tessellate_shape(solid,1.)
        position=adapter.data.xpos[bid];rotation=adapter.data.xmat[bid].reshape(3,3)
        vertices=(np.array(mesh.vertices).reshape(-1,3)/1000-position)@rotation@conversion.T
        parts.append({'id':name,'name':name,'vertices':vertices.reshape(-1).tolist(),'indices':mesh.faces,'color':[int(obj['color'][i:i+2],16)/255 for i in [1,3,5]],'metadata':{'source':'OpenCAD STEP tessellation','runtime_body':name}})
    if layer_geometry is not None:
        for spec in json.loads(layer_geometry.read_text())['layers']:
            name=spec['object_id'];ids[name]=adapter.model.body(name).id
            path=layer_geometry.parent/(name+'.step')
            _,shape=context.execute_operation('import_step',{'filepath':str(path.resolve())},feature_name=name)
            mesh=_tessellate_shape(backend.get_native_shape(shape),.01)
            vertices=np.asarray(mesh.vertices).reshape(-1,3)/1000@conversion.T
            parts.append({'id':name,'name':name,'vertices':vertices.reshape(-1).tolist(),'indices':mesh.faces,'color':[float(v) for v in spec['rgba'].split()[:3]],'metadata':{'source':'OpenCAD STEP tessellation','runtime_body':name,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}})
    selected=trace['frames'][::3]
    if selected[-1]!=trace['frames'][-1]:selected.append(trace['frames'][-1])
    frames=[]
    for frame in selected:
        adapter.data.qpos[:]=frame['qpos'];adapter.data.qvel[:]=frame['qvel'];mujoco.mj_forward(adapter.model,adapter.data)
        poses={name:{'position':(conversion@adapter.data.xpos[bid]).tolist(),'rotation_matrix':(conversion@adapter.data.xmat[bid].reshape(3,3)@conversion.T).reshape(-1).tolist()} for name,bid in ids.items()}
        frames.append({'time':frame['host_time_s'],'poses':poses})
    output.write_text(json.dumps({'parts':parts,'frames':frames,'source_sha256':hashlib.sha256(cad.read_bytes()).hexdigest(),'trace_sha256':hashlib.sha256(trace_path.read_bytes()).hexdigest()},separators=(',',':')))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['configuration','trace','geometry','cad','output']:parser.add_argument(name,type=Path)
    parser.add_argument('--layer-geometry',type=Path)
    args=parser.parse_args();export(args.configuration,args.trace,args.geometry,args.cad,args.output,args.layer_geometry)
