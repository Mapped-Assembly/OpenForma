# Human rigid-block transfer

Independent minimal strict-interaction fixture: one stationary human mannequin,
one workbench, two flat staging trays and one rigid 160 × 150 × 25 mm block.
Four ProcessIR steps (pick, carry, place, withdraw) execute seven action phases:
reach, grasp, lift, carry, lower, release, withdraw. The hands explicitly advertise
move/grasp/carry/release; the block advertises grasp/carry/release. The workbench,
legs and trays are fixed. No independently commanded block pose exists.

The nominal 300 g wood inventory transfers from source to in-hand only after the
grasp phase completes, then from in-hand to destination only after a supported
release. The mass is illustrative, not measured. Both transfers reference native
ProcessIR material streams. HumanIR identifies the synthetic human operator.

Run from the repository root:

```bash
python -m forma_core domains process-runtime examples/process_runtime/rigid-block-transfer/request.json --output /tmp/rigid-block-trace.json
python -m pytest tests/projects/test_rigid_block_transfer.py -q
```

Tests also remove the reach action and raise the release endpoint. Both must fail
without transferring inventory to the destination. Runtime frames contain the
canonical entity poses under `interactions.poses_mm`; visualizers must consume
those poses instead of generating independent object trajectories.

Geometry is pinned to previously compiled native OpenCAD mesh assets, reused with
rigid placement only. Render assets are supplied in the accompanying demo archive;
the regression fixture needs no renderer or geometry engine installation.

Evidence is sampled simulated kinematics and conservative nominal accounting.
No force, friction, full collision, human balance or continuous anatomical
verification is claimed. The mannequin uses the existing 1.75 m, 16-segment mesh.
