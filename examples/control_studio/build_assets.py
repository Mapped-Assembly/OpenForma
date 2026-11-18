"""Build and verify native XY CAD, and package its browser meshes.

Optional --gate-mesh imports the original verified gate mesh export once.
Normal rebuilds reuse the checked-in gate mesh and require no user workspace.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import cadquery as cq
from opencad import KinematicJoint
from opencad.kinematics import evaluate_assembly_pose
from forma_core.control import MachineDefinition

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
OUT=ROOT/"generated/xy-stage"
ASSETS=ROOT/"web/assets"
parser=argparse.ArgumentParser()
parser.add_argument("--gate-mesh",type=Path)
parser.add_argument("--force",action="store_true")
args=parser.parse_args()
OUT.mkdir(parents=True,exist_ok=True); ASSETS.mkdir(parents=True,exist_ok=True)
for extension in ("step","stl"):
    command=[sys.executable,str(REPO/".agents/skills/forma-hardware/scripts/cad.py"),"build",str(ROOT/"cad/xy_stage.py"),
             str(OUT/f"xy-stage.{extension}"),"--tree-output",str(OUT/f"{extension}.tree.json")]
    if args.force:
        command.append("--force")
    result=subprocess.run(command,capture_output=True,text=True)
    if result.returncode:
        print(result.stderr); raise SystemExit(result.returncode)
    summary=json.loads(result.stdout)
    (OUT/f"{extension}-build.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(extension,summary["valid"],summary["features"],"features")

records=json.loads((OUT/"body-map.json").read_text())
definition=MachineDefinition.model_validate_json((ROOT/"definitions/xy-stage.json").read_text())
joints=[KinematicJoint.model_validate(j) for j in json.loads((OUT/"joints.json").read_text())]
solids=list(cq.importers.importStep(str(OUT/"xy-stage.step")).val().Solids())
assert len(solids)==len(records)==22


def bounds(shape):
    b=shape.BoundingBox()
    return [b.xmin,b.ymin,b.zmin,b.xmax,b.ymax,b.zmax]


shapes={}
meshes=[]
remaining=list(solids)
for r in records:
    matches=[s for s in remaining if max(abs(a-b) for a,b in zip(bounds(s),r["bounds_mm"]))<.001]
    assert len(matches)==1,r["id"]
    s=matches[0]; remaining.remove(s)
    assert s.isValid() and len(s.Solids())==1
    shapes[r["id"]]=s
    vertices,triangles=s.tessellate(.15,.12)
    meshes.append(dict(id=r["id"],color=r["color"],positions=[round(v,6) for p in vertices for v in p.toTuple()],
                       indices=[i for t in triangles for i in t]))

groups={r["id"]:r["group"] for r in records}
clashes=[]
sample_count=0
for x in range(-40,41,10):
    for y in range(-30,31,10):
        sample_count+=1
        transforms=evaluate_assembly_pose(joints,{"x_slide":(x+40)/80,"y_slide":(y+30)/60})
        posed={}
        for r in records:
            transform=transforms.get(r["shape_id"])
            expected=(0,0,0) if r["group"]=="fixed" else (x,0,0) if r["group"]=="x" else (x,y,0)
            actual=transform.translation_mm if transform else (0,0,0)
            assert max(abs(a-b) for a,b in zip(actual,expected))<1e-7
            posed[r["id"]]=shapes[r["id"]].translate(actual)
        for a,b in itertools.combinations(posed,2):
            # Same-rigid-group relationships need testing only once.
            if groups[a]==groups[b] and sample_count>1:
                continue
            ba,bb=bounds(posed[a]),bounds(posed[b])
            if any(min(ba[i+3],bb[i+3])-max(ba[i],bb[i])<1e-5 for i in range(3)):
                continue
            volume=sum(s.Volume() for s in posed[a].intersect(posed[b]).Solids())
            if volume>.01:
                clashes.append(dict(x=x,y=y,a=a,b=b,overlap_mm3=volume))
report=dict(passed=not clashes,solid_count=22,grid_poses=sample_count,grid_spacing_mm=10,clashes=clashes,
            hierarchy_transform_check="passed; Y platform inherits X translation",overlap_threshold_mm3=.01,
            scope="Sampled native CAD intersections, not a continuous collision or physical load proof.",
            physical_actuators="Not selected; virtual linear axes only.")
(OUT/"cad-checks.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
assert report["passed"],report
digest=hashlib.sha256((OUT/"xy-stage.step").read_bytes()).hexdigest()
(ASSETS/"xy-stage.json").write_text(json.dumps(dict(units="mm",source_step_sha256=digest,bodies=meshes),separators=(",",":")),encoding="utf-8")
if args.gate_mesh:
    gate=json.loads(args.gate_mesh.read_text())
    assert len(gate["bodies"])==20 and gate.get("source_step_sha256")
    (ASSETS/"servo-gate.json").write_text(json.dumps(gate,separators=(",",":")),encoding="utf-8")
for filename in ("xy-stage","servo-gate"):
    definition=MachineDefinition.model_validate_json((ROOT/f"definitions/{filename}.json").read_text())
    asset=json.loads((ASSETS/definition.model_asset).read_text())
    ids={b["id"] for b in asset["bodies"]}
    assert len(ids)==len(asset["bodies"])
    for axis in definition.axes:
        assert set(axis.joint.body_ids)<=ids
print(json.dumps(report,indent=2))
