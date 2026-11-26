"""Inspect imported STEP assemblies and render static declared views with OpenCAD.

This utility has no execution, motion, thermal, or interaction implementation.
All object identities and presentation transforms come from the JSON request.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from opencad.runtime import RuntimeContext
from opencad.kernel.core.occt_backend import OcctBackend, _tessellate_shape, _bbox_from_shape
from opencad.turntable.render import mesh_arrays, bounding_sphere, fit_distance, render_frame


def run(request_path: Path) -> None:
    """Import, round-trip, inspect and render source geometry without changing it."""
    request: dict[str, Any] = json.loads(request_path.read_text())
    root = request_path.parent
    meshes: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    receipts: dict[str, Any] = {}
    for source in request['sources']:
        backend = OcctBackend()
        context = RuntimeContext(backend=backend)
        feature_id, shape_id = context.execute_operation('import_step', {'filepath': str((root/source['path']).resolve())}, feature_name=source['id'])
        native = backend.get_native_shape(shape_id)
        explorer = TopExp_Explorer(native, TopAbs_SOLID)
        solids = []
        while explorer.More():
            solids.append(explorer.Current()); explorer.Next()
        objects = source['objects']
        if len(solids) != len(objects):
            raise ValueError('Solid count does not match declared identity list')
        mappings = []
        for spec, solid in zip(objects, solids, strict=True):
            bbox = _bbox_from_shape(solid)
            exact_box = Bnd_Box()
            BRepBndLib.AddOptimal_s(solid, exact_box, False, False)
            bounds = exact_box.Get()
            center = (np.array(bounds[:3])+np.array(bounds[3:]))/2
            error = float(np.max(np.abs(center-np.array(spec['center_mm']))))
            mesh = _tessellate_shape(solid, deflection=1.0)
            meshes[spec['id']] = mesh_arrays(mesh.vertices,mesh.faces,mesh.normals)
            mappings.append({'id':spec['id'],'solid_index':len(mappings),'valid':bool(BRepCheck_Analyzer(solid).IsValid()),'center_error_mm':error,'declared_center_match':error<=0.01,'bbox_mm':bbox.model_dump()})
        target = root/(source['id']+'-opencad.step')
        context.export_step(shape_id,str(target))
        context.save_tree_json(str(root/(source['id']+'-opencad.tree.json')))
        _, reimport_id = context.execute_operation('import_step',{'filepath':str(target.resolve())},feature_name='round-trip')
        again = backend.get_native_shape(reimport_id)
        counter = TopExp_Explorer(again,TopAbs_SOLID); count=0
        while counter.More():
            count+=1; counter.Next()
        receipts[source['id']]={'valid':bool(BRepCheck_Analyzer(native).IsValid()),'roundtrip_valid':bool(BRepCheck_Analyzer(again).IsValid()),'solid_count':len(solids),'roundtrip_solid_count':count,'objects':mappings}
    (root/'native-meshes.json').write_text(json.dumps({key:{'vertices':p.reshape(-1).tolist(),'faces':t.reshape(-1).tolist()} for key,(p,t,n) in meshes.items()}))
    for view in request['views']:
        positions=[]; triangles=[]; normals=[]; offset=0
        for instance in view['instances']:
            p,t,n=meshes[instance['object_id']]
            positions.append(p+np.array(instance.get('translation_mm',[0,0,0])))
            triangles.append(t+offset);normals.append(n);offset+=len(p)
        p=np.concatenate(positions);t=np.concatenate(triangles);n=np.concatenate(normals)
        center,_=bounding_sphere(p)
        distance=fit_distance(p,center,np.array([view['azimuth_deg']]),view['elevation_deg'],45,view['width']/view['height'])
        frame=render_frame(p,t,n,center=center,distance=distance,azimuth_degrees=view['azimuth_deg'],elevation_degrees=view['elevation_deg'],width=view['width'],height=view['height'],supersample=2)
        image=Image.new('RGBA',(view['width'],view['height']+110),'#edf1f5')
        image.alpha_composite(Image.fromarray(frame),(0,70))
        draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',22)
        small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',15)
        draw.text((24,14),view['title'],fill='#122633',font=font)
        draw.text((24,44),view['subtitle'],fill='#42596a',font=small)
        draw.text((24,image.height-30),'OpenCAD native geometry | Static view | Performance and fit unverified',fill='#42596a',font=small)
        image.convert('RGB').save(root/view['output'])
    (root/'native-geometry-verification.json').write_text(json.dumps(receipts,indent=2))


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
