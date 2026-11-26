"""Check that the workcell and empirical plan share exact native artifacts."""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import pytest

from examples.chemical_workcell.inspect_workcell import inspect
from forma_core.domains.base import fingerprint
from forma_core.domains.composition import ProductBundle
from forma_core.experiments.models import ExperimentPlan, ExperimentRequest

HERE = Path(__file__).parents[2] / 'examples/chemical_workcell'


def load_case() -> tuple[ProductBundle, ExperimentRequest, ExperimentPlan]:
    """Load the authored native bundle and frozen empirical inputs."""
    return (ProductBundle.model_validate_json((HERE/'product.json').read_text()),
            ExperimentRequest.model_validate_json((HERE/'experiment.json').read_text()),
            ExperimentPlan.model_validate_json((HERE/'plan.json').read_text()))


def test_linked_workcell() -> None:
    """Validate native links while keeping physical verification explicitly absent."""
    bundle, request, plan = load_case()
    report = inspect(bundle, request, plan)
    assert report['equipment_bindings'] == 11
    assert report['physical_status'] == 'not_run'
    assert report['physical_verification_coverage'] == 0
    output = next(o for o in plan.predictions[0].outputs if o.output_id == 'co2.formed_mass')
    assert Fraction(output.value) == Fraction(2) / Fraction('84.0066') * Fraction('44.0095')


def test_revised_equipment_cannot_reuse_old_plan() -> None:
    """Even a valid newly hashed mechanical revision cannot reuse the old prediction pin."""
    bundle, request, plan = load_case()
    ref = next(r for r in bundle.manifest.artifacts if r.domain == 'mechanical')
    document = bundle.artifacts[ref.artifact_id]
    document['revision'] += 1
    document['mechanical']['component_placements'][1]['position']['x_mm'] += 5
    ref.revision = document['revision']
    ref.sha256 = fingerprint(document)
    for link in bundle.manifest.links:
        if link.target.artifact_id == ref.artifact_id:
            link.target.revision = ref.revision
            link.target.sha256 = ref.sha256
    with pytest.raises(ValueError, match='revision mismatch'):
        inspect(bundle, request, plan)


def test_unknown_equipment_is_rejected() -> None:
    """An operation cannot silently bind to an equipment object absent from the model."""
    bundle, request, plan = load_case()
    altered = deepcopy(bundle)
    altered.manifest.links[0].target.object_id = 'missing-equipment'
    with pytest.raises(ValueError):
        inspect(altered, request, plan)
