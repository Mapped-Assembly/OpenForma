"""Validate a native product/experiment pair and render its declared placements.

The renderer shows nominal envelopes, not CAD solids or collision certification.
Run from the repository root: python -m examples.chemical_workcell.inspect_workcell
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from forma_core.domains.base import fingerprint
from forma_core.domains.composition import ProductBundle, validate_product
from forma_core.experiments.evaluation import prepare_experiment, verify_plan
from forma_core.experiments.models import ExperimentPlan, ExperimentRequest
from forma_core.mechanics.models import MechanicalIR

HERE = Path(__file__).parent


def inspect(bundle: ProductBundle, request: ExperimentRequest, plan: ExperimentPlan) -> dict[str, Any]:
    """Check native contracts and exact identity across product, request and frozen plan."""
    validation = validate_product(bundle)
    if not validation.is_valid:
        raise ValueError(validation.model_dump_json())
    verify_plan(plan)
    if prepare_experiment(request).scientific_sha256 != plan.scientific_sha256:
        raise ValueError('Request and frozen plan differ; create a new plan revision.')
    references = {r.artifact_id: r for r in bundle.manifest.artifacts}
    experiment_refs = [plan.process_reference, *[a.reference for a in request.artifacts]]
    if set(references) != {r.artifact_id for r in experiment_refs}:
        raise ValueError('Product and experiment must contain the same artifacts.')
    for ref in experiment_refs:
        other = references[ref.artifact_id]
        if (other.sha256, other.revision, other.domain) != (ref.sha256, ref.revision, ref.domain):
            raise ValueError(f'Product/experiment revision mismatch: {ref.artifact_id}')
    return {
        'product_sha256': fingerprint(bundle), 'scientific_sha256': plan.scientific_sha256,
        'contract_status': 'pass', 'validation': validation.model_dump(mode='json'),
        'equipment_bindings': sum(link.kind == 'uses_equipment' for link in bundle.manifest.links),
        'physical_status': 'not_run', 'physical_verification_coverage': 0,
        'geometry_status': 'nominal_envelopes_only', 'instrument_calibration': 'unknown',
        'measured_material_balance': 'unknown', 'industrial_applicability': 'unknown',
    }


def render(mechanical: MechanicalIR, output: Path) -> None:
    """Render plan and oblique views directly from native placements without case IDs."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    notes = mechanical.mechanical
    if notes is None or not notes.component_placements:
        raise ValueError('MechanicalIR has no placements to render.')
    fig = plt.figure(figsize=(15, 9), facecolor='#f4f6f8')
    ax = fig.add_axes([0.065, 0.37, 0.435, 0.49], facecolor='#f4f6f8')
    iso = fig.add_axes([0.51, 0.34, 0.46, 0.53], projection='3d', facecolor='#f4f6f8')
    colors = plt.get_cmap('tab20')
    bounds = []
    for index, placement in enumerate(notes.component_placements):
        center = [getattr(placement.position, f'{axis}_mm') for axis in 'xyz']
        size = [getattr(placement.size, f'{axis}_mm') for axis in 'xyz']
        if any(getattr(placement.orientation_deg, f'{axis}_deg') != 0 for axis in 'xyz'):
            raise ValueError('This envelope preview supports axis-aligned placements only.')
        if any(s <= 0 for s in size):
            raise ValueError('Envelope dimensions must be positive.')
        x,y,z = [c-s/2 for c,s in zip(center,size)]
        w,d,h = size
        color = '#d8dde4' if placement.layer == 'structural' else colors(index % 20)
        ax.add_patch(Rectangle((x,y),w,d,facecolor=color,edgecolor='#344256',alpha=.65))
        if placement.layer != 'structural':
            ax.text(x+6,y+6,str(index+1),ha='left',va='bottom',weight='bold',fontsize=9)
        vertices = [(x,y,z),(x+w,y,z),(x+w,y+d,z),(x,y+d,z),
                    (x,y,z+h),(x+w,y,z+h),(x+w,y+d,z+h),(x,y+d,z+h)]
        faces = [[vertices[i] for i in indices] for indices in
                 [(0,1,2,3),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]]
        iso.add_collection3d(Poly3DCollection(faces,facecolors=color,edgecolors='#526072',linewidths=.4,alpha=.38))
        if placement.layer != 'structural':
            iso.text(center[0],center[1],z+h+8,str(index+1),fontsize=9)
        bounds.extend(vertices)
        col = index // 6
        row = index % 6
        fig.text(.05+col*.48,.28-row*.032,f'{index+1:02d}  {placement.ref_des} — {placement.label or placement.ref_des}',fontsize=10,color='#20314a')
    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.set_xlabel('Width (mm)'); ax.set_ylabel('Depth (mm)')
    ax.set_title('Plan • operator approaches from bottom',loc='left',weight='bold')
    for axis, setter in zip(range(3),(iso.set_xlim,iso.set_ylim,iso.set_zlim)):
        setter(min(v[axis] for v in bounds),max(v[axis] for v in bounds)+20)
    iso.set_box_aspect(tuple(max(v[a] for v in bounds)-min(v[a] for v in bounds) for a in range(3))); iso.view_init(elev=28,azim=-58)
    iso.set_xlabel('Width (mm)'); iso.set_ylabel('Depth (mm)'); iso.set_zlabel('Height (mm)')
    iso.set_title('Nominal equipment envelopes',loc='left',weight='bold')
    fig.text(.04,.94,notes.physical_form,fontsize=21,weight='bold',color='#172b49')
    fig.text(.04,.90,'DECLARATIVE MECHANICALIR  /  conceptual layout  /  no physical trial',fontsize=11,color='#526072')
    fig.text(.04,.045,'Dimensions and placements are design assumptions. Open vessels appear as envelopes; lids are not specified.\nNo collision, capacity, calibration or physical performance certification is implied.',fontsize=10,color='#526072')
    for suffix in ('png','svg'):
        fig.savefig(output / f'layout.{suffix}',dpi=160,facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> None:
    """Inspect any compatible native declaration directory and export a report and layout."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',nargs='?',type=Path,default=HERE)
    parser.add_argument('--render',action='store_true',help='Requires matplotlib for the optional preview.')
    args=parser.parse_args()
    directory=args.directory
    bundle=ProductBundle.model_validate_json((directory/'product.json').read_text())
    request=ExperimentRequest.model_validate_json((directory/'experiment.json').read_text())
    plan=ExperimentPlan.model_validate_json((directory/'plan.json').read_text())
    report=inspect(bundle,request,plan)
    (directory/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    for ref in bundle.manifest.artifacts:
        if ref.domain != 'mechanical':
            continue
        mechanical=MechanicalIR.model_validate(bundle.artifacts[ref.artifact_id])
        with (directory/'equipment.csv').open('w',newline='') as file:
            writer=csv.writer(file)
            writer.writerow(['ref_des','label','width_mm','depth_mm','height_mm','basis'])
            for placement in mechanical.mechanical.component_placements if mechanical.mechanical else []:
                writer.writerow([placement.ref_des,placement.label,*placement.size.model_dump().values(),'assumed layout envelope; supplier and price unverified'])
        if args.render:
            render(mechanical,directory)
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
