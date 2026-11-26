# HumanIR foundation (1.0)

Human participants are native, independent domain artifacts. BiologicalIR
continues to own experiments; MechanicalIR owns engineered geometry;
ThermalIR owns heat-transfer analysis; ProcessIR owns operation topology.
HumanIR owns declared human profiles, roles, capabilities and constraints.
OpenCAD owns geometric realization; scene instances and runtime execution
are companion work, not implemented by this schema.

## Authoring path

1. Select the explicit domain "human" in the existing registry-backed domain
   selection (ProductScope.domains); no new boolean scope flags are required.
2. Provide HumanContext with scope, participant_requirements and
   evidence_requirements. Conceptual unknowns must stay explicit.
3. Author the standalone HumanIR JSON using the schema from
   domain_model("human").model_json_schema(). Use stable profile, participant,
   segment, joint and interface IDs; pin each participant to its profile revision.
4. Parse with parse_domain and inspect validate_domain findings. Persist and
   compose through the existing native domain APIs, not a separate registry.
5. Use participant IDs and declared port IDs in revision-pinned relationships
   supported by both domains. This first slice advertises same_object and
   spatial only; dedicated contact, heat-transfer, control and task-assignment
   relationship validators remain follow-up work.
6. Keep unknown measurements, physical testing and safety qualification separate.
   No evaluator or human behavior simulator is advertised.

## Declarative fixtures

- examples/domains/human-wearable.json: wearer, torso, mechanical contact,
  thermal interface and local control; skin temperature bounds are deliberately
  unknown. Inspired by Form issue 283, not a complete wearable design.
- examples/domains/human-workcell.json: a manual assembly participant with
  articulated arm, declared task capability and unknown performance values.

From the repository root:

    python -m unittest discover -s tests -p test_human_ir.py

For a standalone inspection:

    python -c 'import json; from forma_core.domains.registry import parse_domain, validate_domain; p=json.load(open("examples/domains/human-wearable.json")); print(parse_domain(p).model_dump_json(indent=2)); print(validate_domain(p))'

Units are fixed in field names (m, kg, deg, N, s), and constraint quantity
determines its unit (skin_temperature: degC; contact_force: N; reach: m;
exposure_duration: s). Null means unknown, not zero or a safe value. Joint
limits describe a declared interval, not a calibrated kinematic model.

## Remaining scope

Issue #62 remains open: full cross-domain binding semantics and integrated
wearable/workcell generation, thermal and biomechanical properties beyond this
minimal profile, empirical evidence integration, and end-to-end CLI/MCP
verification remain to be completed. Companion issues OpenCAD #15,
OpenIndustries #117, and OpenForma #63 cover geometry, scenes and execution.
