"""Native OpenCAD two-axis kinematic benchmark, mm, Z up.

Conceptual linear-bearing envelopes, not a manufactured or motorized design.
X translates the whole Y mechanism; Y translates its platform and tool marker.
"""
import json
from pathlib import Path
import cadquery as cq
from opencad import Part, Sketch, get_default_context
from forma_core.config import config

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(config.get("FORMA_CONTROL_CAD_OUT", str(ROOT/"generated/xy-stage")))
OUT.mkdir(parents=True, exist_ok=True)
ctx = get_default_context()
parts, records = {}, []


def box(name, size, center):
    return Part(name=name).box(*size, name=name).translate(tuple(center), name=name+" placement")


def rod(name, radius, length, origin, plane):
    return Part(name=name).extrude(Sketch(plane=plane, origin=tuple(origin), name=name+" profile").circle(radius), depth=length, name=name)


def add(id, part, group, color):
    parts[id] = part
    native = cq.Shape(ctx.kernel.get_native_shape(part.shape_id))
    b = native.BoundingBox()
    records.append(dict(id=id, group=group, color=color, shape_id=part.shape_id, feature_id=part.feature_id,
                        bounds_mm=[b.xmin,b.ymin,b.zmin,b.xmax,b.ymax,b.zmax], volume_mm3=native.Volume()))


base = box("Frame base", (200,160,8), (0,0,4))
for x in (-90,90):
    for y in (-70,70):
        base.cut(rod("Base mounting hole",2.2,10,(x,y,-1),"XY"),name="Base mounting clearance")
add("base",base,"fixed",[.20,.29,.38])
for i,y in enumerate((-50,50),1):
    add(f"X_rail_{i}",rod("X linear rail",3,160,(-80,y,20),"YZ"),"fixed",[.70,.76,.82])
for i,(x,y) in enumerate([(x,y) for x in (-85,85) for y in (-50,50)],1):
    add(f"X_mount_{i}",box("X rail end support",(10,14,18),(x,y,17)),"fixed",[.38,.48,.59])

add("X_plate",box("X carriage platform",(50,124,6),(0,0,29)),"x",[.10,.49,.69])
for i,(x,y) in enumerate([(x,y) for x in (-16,16) for y in (-50,50)],1):
    bearing=box("X bearing envelope",(12,12,12),(x,y,20))
    bearing.cut(rod("X rail clearance",3.4,14,(x-7,y,20),"YZ"),name="X bearing bore")
    add(f"X_bearing_{i}",bearing,"x",[.58,.64,.69])
for i,x in enumerate((-16,16),1):
    add(f"Y_rail_{i}",rod("Y linear rail",2.5,96,(x,-48,42),"XZ"),"x",[.70,.76,.82])
for i,(x,y) in enumerate([(x,y) for x in (-16,16) for y in (-53,53)],1):
    add(f"Y_mount_{i}",box("Y rail end support",(12,10,18),(x,y,41)),"x",[.15,.40,.54])

add("Y_plate",box("Y tool platform",(44,28,6),(0,0,51)),"y",[.98,.46,.13])
for i,x in enumerate((-16,16),1):
    bearing=box("Y bearing envelope",(12,18,12),(x,0,42))
    bearing.cut(rod("Y rail clearance",2.8,20,(x,-10,42),"XZ"),name="Y bearing bore")
    add(f"Y_bearing_{i}",bearing,"y",[.58,.64,.69])
add("tool_marker",rod("Non-cutting tool marker",4,16,(0,0,54),"XY"),"y",[.99,.72,.22])


def joint(id, kind, parent, child, axis, lo=0, hi=0):
    result=ctx.registry.call("create_kinematic_joint",dict(joint_id=id,type=kind,
        parent_shape_id=parts[parent].shape_id,child_shape_id=parts[child].shape_id,axis=axis,
        origin_mm=(0,0,0),lower_limit=lo,upper_limit=hi,
        metadata={"body_id":child,"forma_target_ref":child}))
    if not result.ok:
        raise RuntimeError(result.message)


joint("x_slide","prismatic","base","X_plate",(1,0,0),-40,40)
joint("y_slide","prismatic","X_plate","Y_plate",(0,1,0),-30,30)
for r in records:
    if r["group"]=="x" and r["id"]!="X_plate":
        joint("fixed_"+r["id"],"fixed","X_plate",r["id"],(0,0,1))
    if r["group"]=="y" and r["id"]!="Y_plate":
        joint("fixed_"+r["id"],"fixed","Y_plate",r["id"],(0,0,1))

FORMA_EXPORT_SHAPE_IDS=[p.shape_id for p in parts.values()]
assembly=cq.Assembly(name="XY_stage_CONCEPT")
for group in ("fixed","x","y"):
    branch=cq.Assembly(name=group)
    for r in records:
        if r["group"]==group:
            branch.add(cq.Shape(ctx.kernel.get_native_shape(r["shape_id"])),name=r["id"],color=cq.Color(*r["color"]))
    assembly.add(branch)
assembly.save(str(OUT/"xy-stage-hierarchy.step"))
(OUT/"body-map.json").write_text(json.dumps(records,indent=2)+"\n",encoding="utf-8")
(OUT/"joints.json").write_text(json.dumps([j.model_dump(mode="json") for j in ctx.kernel.joint_store.all()],indent=2)+"\n",encoding="utf-8")
