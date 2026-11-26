# Chemical workcell with visible runtime execution

`request.json` is instance data, not a custom controller. It uses the unchanged
chemical experiment plan from the parent example and binds its six steps to 27
phases in the generic OpenForma virtual workcell host. All observations are
synthetic. The moving geometry represents virtual human manipulation; physical
actuators are not specified.

`workcell.json` and `assembly.json` are the original declarative OpenCAD geometry
and component identities. `workcell.step` is their existing native geometry
export. `scene.json` contains tessellated STEP solids with those component IDs,
plus empty intermediate frames for the declared virtual motion hierarchy. Its
source STEP hash is retained; the request pins the canonical scene hash. The
machine package labels this separately authored motion model as `external`, not
as native OpenCAD kinematic identity.

Run using the commands in [virtual-workcells.md](../../../docs/virtual-workcells.md).
The trace and GIF are generated outputs; no case-specific executable is required.

Expect all six process steps and 27 machine receipts completed, 50 g vinegar and
2 g bicarbonate transferred into the bowl, and zero virtual inventory residual.
The bowl remains a 52 g **unreacted bookkeeping inventory**: reaction dynamics are
unsupported. The separately supplied synthetic final-mass observation is used only
by the original empirical comparison. Physical verification remains not run.
