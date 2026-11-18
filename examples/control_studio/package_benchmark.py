"""Bind benchmark geometry/control to typed Forma mechanical and project artifacts."""
import hashlib
import json
from pathlib import Path
import uuid

from forma_core.control import MachineDefinition
from forma_core.domains.registry import parse_domain, validate_domain
from forma_core.opencode.architecture import reconcile_architecture
from forma_core.validation import build_validation_summary, validate_project
from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"generated/xy-stage"
records=json.loads((OUT/"body-map.json").read_text())
report=json.loads((OUT/"cad-checks.json").read_text())
assert report["passed"]
control=MachineDefinition.model_validate_json((ROOT/"definitions/xy-stage.json").read_text())
project_id=str(uuid.uuid5(uuid.NAMESPACE_URL,"https://forma.local/benchmarks/two-axis-xy-v1"))


def save(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2)+"\n",encoding="utf-8")


def node(id,name,purpose,children=(),interfaces=()):
    return dict(system_id=id,name=name,domain="mechanical" if id!="product.control" else "control",
                purpose=purpose,responsibilities=[purpose],detail_owner="benchmark design",children=list(children),interfaces=list(interfaces))


architecture=dict(summary="Two serial linear axes controlled through one reusable runtime.",root=node("product","XY stage benchmark",
    "Exercise multi-axis control, CAD bindings and feedback provenance",children=[
        node("product.frame","Stationary frame","Support two X-axis rails and define the world coordinate system"),
        node("product.x_stage","X carriage","Translate the entire Y mechanism along X",interfaces=[
            dict(name="X rail interface",connects_to="product.frame",purpose="Prismatic X travel -40..40 mm")]),
        node("product.y_stage","Y platform","Translate the tool marker along local Y while inheriting X motion",interfaces=[
            dict(name="Y rail interface",connects_to="product.x_stage",purpose="Prismatic local Y travel -30..30 mm")]),
        node("product.control","Shared machine runtime","Own axis limits, coordinated plans, sessions and adapter feedback",interfaces=[
            dict(name="X command",connects_to="product.x_stage",purpose="Named mm target, mm/s rate and explicit simulated feedback"),
            dict(name="Y command",connects_to="product.y_stage",purpose="Named mm target, mm/s rate and explicit simulated feedback")])]))
parts=[];components=[];views=[];mechanical_parts=[];placements=[];bindings=[]
for group,ref,name,owner in [("fixed","B1","Frame assembly","product.frame"),("x","X1","X carriage assembly","product.x_stage"),("y","Y1","Y platform assembly","product.y_stage")]:
    bodies=[r for r in records if r["group"]==group]
    lo=[min(r["bounds_mm"][i] for r in bodies) for i in range(3)]
    hi=[max(r["bounds_mm"][i+3] for r in bodies) for i in range(3)]
    size=[hi[i]-lo[i] for i in range(3)]
    center=[(hi[i]+lo[i])/2 for i in range(3)]
    part_id="XY_BENCH_"+ref
    parts.append(dict(part_definition_id=part_id,part_number=part_id,name=name,category="Mechanical",
                      description="Logical CAD assembly rollup, not a quoted purchased part.",dimensions_mm=dict(zip(("x","y","z"),size))))
    components.append(dict(ref_des=ref,part_definition_id=part_id,rationale=name,
                           configuration={"system_id":owner,"procurement_status":"virtual benchmark; not selected"}))
    views.append(dict(object_id=project_id+"."+ref,ref_des=ref,part_definition_id=part_id))
    mechanical_parts.append(dict(part_definition_id=part_id,dimensions_mm=dict(zip(("x","y","z"),size)),
                                 geometry_refs=["xy-stage-hierarchy.step#"+r["id"] for r in bodies]))
    placements.append(dict(ref_des=ref,label=name,layer="structural" if ref=="B1" else "mechanism",
                            position=dict(zip(("x_mm","y_mm","z_mm"),center)),size=dict(zip(("x_mm","y_mm","z_mm"),size)),
                            notes="Neutral-pose world bounding box of assembly members."))
    bindings.extend(dict(body_id=r["id"],ref_des=ref,system_id=owner,axis_id=None if group=="fixed" else group) for r in bodies)
mechanical=dict(physical_form="Open-frame two-axis linear stage",enclosure_type="Open benchmark frame",
    mounting_guidance="Concept geometry only; clearance sleeves are not actual bearing selections.",
    manufacturability_rating="Virtual benchmark; not production/fabrication qualified",
    render_dimensions=dict(x_mm=200,y_mm=160,z_mm=70),component_placements=placements,
    fabrication_details=["All dimensions are authored benchmark assumptions.","No motors, drives, wiring or physical position sensors selected."],
    motion_intents=[dict(motion_id="x_slide",type="prismatic",target_ref="X1",parent_ref="B1",axis="X",min_mm=-40,max_mm=40,pivot_mm=[0,0,0]),
                    dict(motion_id="y_slide",type="prismatic",target_ref="Y1",parent_ref="X1",axis="Y",min_mm=-30,max_mm=30,pivot_mm=[0,0,0])])
cad=dict(adapter="forma-opencad",path="xy-stage-hierarchy.step",step_path="xy-stage.step",stl_path="xy-stage.stl",
         tree_path="stl.tree.json",units="mm",joint_definitions_path="joints.json",fidelity="conceptual benchmark")
mech=dict(domain="mechanical",schema_version="1.0",artifact_id=project_id+".mechanical",revision=1,
          parts=mechanical_parts,components=views,mechanical=mechanical,cad_model=cad)
parsed=parse_domain(mech)
findings=[f.model_dump(mode="json") for f in validate_domain(parsed)]
assert not any(f["severity"]=="error" for f in findings)
ir=HardwareIntermediateRepresentation.model_validate(dict(
    overview=dict(title="Two-axis XY control benchmark",description="Virtual serial XY stage for reusable machine-control verification.",difficulty="Intermediate",category="Robotics"),
    part_definitions=parts,components=components,system_architecture=architecture,mechanical=mechanical,cad_model=cad,
    assembly_metadata=dict(control_definition="control-definition.json",mechanical_artifact="mechanical-ir.json",
        benchmark_report="../control-benchmark.json",physical_status="not implemented",price_status="unquoted",bom_semantics="three logical assembly rollups, not procurement items")))
reconcile_architecture(ir,None)
issues=validate_project(ir)
ir.validation=build_validation_summary(issues)
ir.is_valid=not ir.validation.critical
assert ir.is_valid, [i.model_dump(mode="json") for i in issues]
for axis in control.axes:
    assert axis.component_ref in {c["ref_des"] for c in components}
    assert all(b["ref_des"]==axis.component_ref for b in bindings if b["body_id"] in axis.joint.body_ids)
save("mechanical-ir.json",parsed.model_dump(mode="json"))
save("control-definition.json",control.model_dump(mode="json"))
save("system-architecture.json",architecture)
save("subsystem-bindings.json",bindings)
save("forma-project.json",dict(format="forma-project",version=1,project_id=project_id,title=ir.overview.title,
    visibility="private",workspace_id=None,prompt="Build a two-axis benchmark for reusable real-time machine control.",project_ir=ir.model_dump(mode="json"),artifacts=[]))
save("forma-validation.json",dict(is_valid=ir.is_valid,mechanical_findings=findings,hardware_findings=[i.model_dump(mode="json") for i in issues]))
save("checksums.json",{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file() and p.name!="checksums.json"})
print("Forma benchmark package validated: 3 assembly instances, 22 bodies, 2 control-to-CAD axis bindings.")
