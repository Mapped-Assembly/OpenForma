"""Compile declared box layers through OpenCAD and bind them into a MuJoCo scene."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
import numpy as np
from opencad.runtime import RuntimeContext
from opencad.kernel.core.occt_backend import OcctBackend, _tessellate_shape


def walk(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all declarative MJCF nodes in traversal order."""
    return [node] + [item for child in node.get('children', []) for item in walk(child)]


def compile_layers(request_path: Path) -> None:
    """Create native STEP, local OBJ and fixed visual bodies from the JSON dimensions."""
    root=request_path.parent
    request=json.loads(request_path.read_text())
    configuration=json.loads((root/'configuration-base.json').read_text())
    nodes=walk(configuration['scene']); assets=next(n for n in nodes if n['tag']=='asset')
    sources=[]; instances=[]
    for index, spec in enumerate(request['layers']):
        oid=spec['object_id']; dimensions=spec['dimensions_mm']; center=spec['center_local_mm']
        backend=OcctBackend(); context=RuntimeContext(backend=backend)
        _,shape=context.execute_operation('create_box',dict(zip(('length','width','height'),dimensions)),feature_name=oid)
        # Native boxes are centered at the origin; apply the declared local center.
        _,shape=context.execute_operation('translate',{'shape_id':shape,'offset':center},feature_name='placement')
        context.export_step(shape,str((root/(oid+'.step')).resolve()))
        context.save_tree_json(str(root/(oid+'.tree.json')))
        native=backend.get_native_shape(shape)
        mesh=_tessellate_shape(native,deflection=.01)
        vertices=np.asarray(mesh.vertices).reshape(-1,3)/1000
        faces=np.asarray(mesh.faces).reshape(-1,3)+1
        path=root/'assets'/(oid+'.obj')
        path.write_text('\n'.join(['v '+' '.join(map(str,v)) for v in vertices]+['f '+' '.join(map(str,f)) for f in faces])+'\n')
        configuration['asset_hashes']['assets/'+oid+'.obj']=hashlib.sha256(path.read_bytes()).hexdigest()
        assets['children'].append({'tag':'mesh','attributes':{'name':oid+'-mesh','file':'assets/'+oid+'.obj'},'children':[]})
        parent=next(n for n in nodes if n['tag']=='body' and n['attributes'].get('name')==spec['parent_body'])
        parent['children'].append({'tag':'body','attributes':{'name':oid},'children':[{'tag':'geom','attributes':{'name':oid+'-visual','type':'mesh','mesh':oid+'-mesh','rgba':spec['rgba'],'contype':'0','conaffinity':'0','mass':'0'},'children':[]}]})
        configuration['attachments'].append({'body':oid,'parent':spec['parent_body']})
        sources.append({'id':oid,'path':oid+'.step','objects':[{'id':oid,'center_mm':center}]})
        instances.append({'object_id':oid,'translation_mm':[0,-index*14,0]})
    configuration['human']['sha256']=hashlib.sha256((root/'human.json').read_bytes()).hexdigest()
    (root/'configuration.json').write_text(json.dumps(configuration,indent=2)+'\n')
    views={'sources':sources,'views':[{'title':'V3 controller laminate | Native OpenCAD exploded view','subtitle':'Substrate 0.100 mm / copper envelope 0.035 mm / coverlay 0.165 mm; candidate dimensions','width':1100,'height':620,'azimuth_deg':-55,'elevation_deg':24,'instances':instances,'output':'layer-exploded.png'}]}
    (root/'layer-cad-review.json').write_text(json.dumps(views,indent=2)+'\n')


if __name__=='__main__':
    compile_layers(Path(sys.argv[1]).resolve())
