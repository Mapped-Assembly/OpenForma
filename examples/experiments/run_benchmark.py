"""Run declarative empirical benchmark cases; synthetic data never become physical evidence.

Usage: python -m examples.experiments.run_benchmark examples/experiments/water-mixing-suite.json
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
from pathlib import Path
from typing import Any

from forma_core.domains.base import fingerprint
from forma_core.experiments import ExperimentRequest, ExperimentRun, prepare_experiment, compare_experiment
from forma_core.experiments.tools import experiment_tool


def run_suite(suite: dict[str, Any]) -> dict[str, Any]:
    """Apply explicit input edits and compare declared synthetic readings using public APIs."""
    base = experiment_tool({'action': 'benchmark', 'benchmark_id': suite['benchmark_id']})
    cases = []
    for index, case in enumerate(suite['cases']):
        authored = deepcopy(base)
        authored['experiment_id'] += '.' + case['case_id']
        for edit in case.get('edits', []):
            target = authored
            for key in edit['path'][:-1]:
                target = target[key]
            target[edit['path'][-1]] = edit['value']
        for artifact in authored['artifacts']:
            artifact['artifact']['revision'] = index + 1
            artifact['reference']['revision'] = index + 1
            artifact['reference']['sha256'] = fingerprint(artifact['artifact'])
        plan = prepare_experiment(ExperimentRequest.model_validate(authored))
        criterion = next(c for c in plan.request.criteria if c.criterion_id == case['criterion_id'])
        started = datetime(2026, 1, 1, tzinfo=timezone.utc)
        def reading(value: float, unit: str, uncertainty: float | None) -> dict[str, Any]:
            """Make explicitly synthetic instrument inputs with declared precision."""
            return dict(value=value, unit=unit, instrument='synthetic benchmark instrument', resolution=0.1, uncertainty=uncertainty)
        run = ExperimentRun.model_validate(dict(run_id=case['case_id'], sample_id=case['case_id'], kind='synthetic',
            plan_sha256=plan.scientific_sha256, started_at=started, completed_at=started+timedelta(seconds=60),
            operator='deterministic software benchmark',
            inputs={i.input_id: reading(float(Fraction(i.value)), i.unit, 0.1) for i in plan.input_requirements},
            conditions={c.name: reading((c.minimum+c.maximum)/2, c.unit, 0.1) for c in plan.request.conditions},
            observations=[dict(observation_id='declared-synthetic-reading', criterion_id=criterion.criterion_id,
                target=criterion.target.model_dump(mode='json'), basis=criterion.basis, assumptions_confirmed=True,
                readings=[reading(case['observation']['value'], case['observation']['unit'], case['observation']['uncertainty'])],
                evidence_ids=['synthetic-source'])],
            evidence=[dict(source_id='synthetic-source', basis='illustrative', title='Declared software fixture',
                           rationale='Synthetic benchmark only; no physical experiment was performed.')]))
        report = compare_experiment(plan, [run])
        result = report['results'][0]
        checks = {'reference_matches_independent_value': Fraction(result['reference']) == Fraction(case['expected_reference']),
                  'verdict_matches': result['status'] == case['expected_status'],
                  'physical_status_not_run': report['physical_status'] == 'not_run',
                  'physical_coverage_zero': report['verification_coverage'] == 0}
        cases.append({'case_id': case['case_id'], 'checks': checks, 'passed': all(checks.values()),
                      'plan_sha256': plan.scientific_sha256, 'comparison_input_digest': report['input_digest'],
                      'prediction': plan.predictions[0].model_dump(mode='json'), 'comparison': result,
                      'physical_status': report['physical_status'], 'verification_coverage': report['verification_coverage']})
    return {'format':'forma.declarative-benchmark', 'suite_sha256':fingerprint(suite),
            'benchmark_id':suite['benchmark_id'], 'evidence_kind':'synthetic',
            'physical_experiments_performed':0, 'passed':sum(c['passed'] for c in cases), 'total':len(cases),
            'cases':cases}


def main() -> int:
    """Write a portable deterministic result and return failure on any benchmark mismatch."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', type=Path)
    parser.add_argument('--output', type=Path)
    args=parser.parse_args()
    report=run_suite(json.loads(args.suite.read_text()))
    text=json.dumps(report,indent=2)+'\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text,end='')
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
