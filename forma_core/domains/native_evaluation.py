"""Named native evaluator adapters; no benchmark IDs or experiment-specific branches."""
from __future__ import annotations

from typing import Any
from fractions import Fraction

from .evaluation import EvaluationOutput, EvaluationRequest, EvaluationSpec
from .providers import ArtifactReference


def evaluation_specs(domain: str) -> list[EvaluationSpec]:
    """Advertise reusable scientific models through the existing domain registry."""
    if domain == 'chemical':
        from forma_core.chemistry.stoichiometry import StoichiometryRequest
        return [EvaluationSpec(evaluator_id='stoichiometric-bound', version='1.0',
            description='Single forward reaction batch bound with supplied inventory/properties.',
            parameter_schema=StoichiometryRequest.model_json_schema(),
            outputs={'<species>.formed': 'mol, net_formation', '<species>.final': 'mol, total_inventory',
                     '<species>.formed_mass': 'g, net_formation', '<species>.final_mass': 'g, total_inventory'},
            limitations=['No actual conversion, kinetics or inferred properties.'])]
    if domain == 'thermal':
        from forma_core.thermal.equilibrium import EquilibriumRequest
        return [EvaluationSpec(evaluator_id='adiabatic-equilibrium', version='1.0',
            description='Equilibrium endpoint of declared contacting constant-capacity bodies.',
            parameter_schema=EquilibriumRequest.model_json_schema(),
            outputs={'final_temperature': 'K, equilibrium_temperature', 'energy_residual': 'J, modeled_energy_balance',
                     '<node>.energy_change': 'J, sensible_energy_change'},
            limitations=['Closed adiabatic system; no phase change, reaction, variable capacity or rate model.']),
            EvaluationSpec(evaluator_id='lumped-resistance', version='1.0',
                description='Existing single-zone steady-state thermal evaluator.',
                parameter_schema={'type':'object','properties':{'case_id':{'type':'string'}},'required':['case_id'],'additionalProperties':False},
                outputs={'zone_temperature': 'K, steady_state_temperature', 'net_heat': 'W, net_heat_rate',
                         'energy_residual': 'W, modeled_power_balance'},
                limitations=['One zone, one ambient and one lumped connection.'])]
    return []


def evaluate_named(payload: dict[str, Any], reference: ArtifactReference, request: EvaluationRequest) -> dict[str, Any]:
    """Translate domain-owned numerical receipts to named dimensioned outputs."""
    outputs: list[EvaluationOutput] = []
    if reference.domain == 'chemical' and request.evaluator_id == 'stoichiometric-bound':
        from forma_core.chemistry.models import ChemicalIR
        from forma_core.chemistry.stoichiometry import StoichiometryRequest, evaluate_stoichiometry
        raw = evaluate_stoichiometry(ChemicalIR.model_validate(payload), StoichiometryRequest.model_validate(request.parameters)).model_dump(mode='json')
        for amount in raw['amounts']:
            for name, field, dimension, unit, basis in (
                ('formed', 'formed', 'amount', 'mol', 'net_formation'),
                ('final', 'final', 'amount', 'mol', 'total_inventory'),
                ('formed_mass', 'formed_mass_g', 'mass', 'g', 'net_formation'),
                ('final_mass', 'final_mass_g', 'mass', 'g', 'total_inventory'),
            ):
                value = amount[field]
                outputs.append(EvaluationOutput(output_id=f"{amount['species_id']}.{name}", value=value,
                    dimension=dimension, unit=unit, basis=basis,
                    kind='upper_bound' if basis == 'net_formation' or Fraction(amount['net_coefficient']) >= 0 else 'prediction',
                    status=raw['status'] if value is not None else 'unknown', message='Conditional forward stoichiometric bound.'))
    elif reference.domain == 'thermal':
        from forma_core.thermal.models import ThermalIR
        document = ThermalIR.model_validate(payload)
        if request.evaluator_id == 'adiabatic-equilibrium':
            from forma_core.thermal.equilibrium import EquilibriumRequest, evaluate_equilibrium
            raw = evaluate_equilibrium(document, EquilibriumRequest.model_validate(request.parameters))
            specs = [('final_temperature', 'final_temperature_kelvin', 'temperature', 'K', 'equilibrium_temperature', 'prediction'),
                     ('energy_residual', 'energy_residual_j', 'energy', 'J', 'modeled_energy_balance', 'conservation_residual')]
            for output_id, field, dimension, unit, basis, kind in specs:
                outputs.append(EvaluationOutput(output_id=output_id, value=raw.get(field), dimension=dimension,
                    unit=unit, basis=basis, kind=kind, status=raw['status'], message=raw['message']))
            for node_id, value in raw.get('energy_changes_j', {}).items():
                outputs.append(EvaluationOutput(output_id=f'{node_id}.energy_change', value=value,
                    dimension='energy', unit='J', basis='sensible_energy_change', status=raw['status']))
        else:
            from forma_core.thermal.validation import evaluate_case
            if set(request.parameters) != {'case_id'}:
                raise ValueError('lumped-resistance parameters require only case_id.')
            case = next((c for c in document.cases if c.case_id == request.parameters['case_id']), None)
            if case is None:
                raise ValueError('Unknown thermal case.')
            raw = evaluate_case(document, case).model_dump(mode='json')
            for output_id, field, dimension, unit, basis, kind in (
                ('zone_temperature', 'zone_temperature_kelvin', 'temperature', 'K', 'steady_state_temperature', 'prediction'),
                ('net_heat', 'net_heat_w', 'heat_rate', 'W', 'net_heat_rate', 'prediction'),
                ('energy_residual', 'residual_w', 'heat_rate', 'W', 'modeled_power_balance', 'conservation_residual'),
            ):
                outputs.append(EvaluationOutput(output_id=output_id, value=raw.get(field), dimension=dimension,
                    unit=unit, basis=basis, kind=kind, status=raw['status'], message=raw['message']))
    else:
        raise ValueError('Unsupported native evaluator.')
    return {'status': raw['status'], 'outputs': [o.model_dump(mode='json') for o in outputs], 'raw': raw,
            'message': raw.get('message', 'Inspect the domain-owned calculation receipt.')}
