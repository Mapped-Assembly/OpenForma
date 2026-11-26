"""Manual, model-only and existing machine-runtime execution boundaries."""
from __future__ import annotations

from typing import Any, Protocol

from forma_core.domains.registry import get_registry
from forma_core.domains.providers import DomainRegistry
from .models import AdapterResult, ProcessExecutionPlan, StepBinding, StepSubmission


class ProcessAdapter(Protocol):
    """Perform or acknowledge a step; the process runtime owns completion gates."""

    def evidence_basis(self, plan: ProcessExecutionPlan, binding: StepBinding) -> str:
        """Resolve measurement provenance before any execution side effect."""
        ...

    def execute(self, plan: ProcessExecutionPlan, binding: StepBinding,
                submission: StepSubmission) -> AdapterResult:
        """Return evidence without mutating the process execution state."""
        ...


class ManualAdapter:
    """A human acknowledgement; no machine command or prediction is a measurement."""

    def evidence_basis(self, plan: ProcessExecutionPlan, binding: StepBinding) -> str:
        """Manual submissions assert actual operator records."""
        return "measured"

    def execute(self, plan: ProcessExecutionPlan, binding: StepBinding,
                submission: StepSubmission) -> AdapterResult:
        """Require explicit acknowledgement before testing the recorded obligations."""
        return AdapterResult(status='ready' if submission.acknowledged else 'unknown',
                             provenance='manual', message='Human acknowledgement recorded.' if submission.acknowledged else 'Awaiting human acknowledgement.')


class VirtualProcessAdapter:
    """Run existing versioned scientific evaluators; all submitted readings are illustrative."""

    def __init__(self, registry: DomainRegistry | None = None) -> None:
        """Use the same domain-provider registry as empirical prediction."""
        self.registry = get_registry(registry)

    def evidence_basis(self, plan: ProcessExecutionPlan, binding: StepBinding) -> str:
        """Virtual scenario inputs are always illustrative."""
        return "illustrative"

    def execute(self, plan: ProcessExecutionPlan, binding: StepBinding,
                submission: StepSubmission) -> AdapterResult:
        """Preserve unsupported/unknown model outputs without fabricating observations."""
        receipts = []
        if binding.evaluations:
            assert plan.experiment is not None
            request = plan.experiment.request
            artifacts = {a.reference.artifact_id: a for a in request.artifacts}
            for eid in binding.evaluations:
                evaluation = next(e for e in request.evaluations if e.evaluation_id == eid)
                artifact = artifacts.get(evaluation.artifact_id)
                if artifact is None:
                    return AdapterResult(status='unsupported', provenance='simulated',
                                         message='Evaluation requires an explicitly supplied source artifact.')
                receipts.append(self.registry.evaluate_named(artifact.artifact, artifact.reference, evaluation))
        statuses = {r.status for r in receipts} | {o.status for r in receipts for o in r.outputs}
        status = 'failed' if 'fail' in statuses else 'unsupported' if 'unsupported' in statuses else 'unknown' if 'unknown' in statuses else 'ready'
        if not submission.acknowledged and status == 'ready':
            status = 'unknown'
        return AdapterResult(status=status, provenance='simulated', evaluations=receipts,
                             message='Virtual workflow only; model results are predictions, never measured quantities.')


class MachineClient(Protocol):
    """Existing ControlClient subset; the host owns claiming, arming and watchdog traffic."""

    def capabilities(self) -> dict[str, Any]:
        """Read the existing control service capability and package record."""
        ...
    def command(self, op: str, **parameters: Any) -> dict[str, Any]:
        """Send a command using the host-owned control session."""
        ...
    def action_result(self, run_id: str) -> dict[str, Any]:
        """Read an action receipt from the authoritative machine runtime."""
        ...


class MachineProcessAdapter:
    """Dispatch once to an explicitly supplied control client; poll runtime evidence."""

    def __init__(self, client: MachineClient, machine_id: str) -> None:
        """Do not discover URLs, claim ownership, arm hardware or retry dispatch implicitly."""
        self.client = client
        self.machine_id = machine_id
        self.pending: dict[str, str | None] = {}
        self.owner_run: str | None = None

    def evidence_basis(self, plan: ProcessExecutionPlan, binding: StepBinding) -> str:
        """Read the pinned machine backend before accepting any observation provenance."""
        from forma_core.control.package import MachinePackage
        raw = self.client.capabilities().get("machine_package")
        if raw is None:
            return "measured"
        package = MachinePackage.model_validate(raw)
        if binding.machine is None or package.sha256 != binding.machine.package_sha256:
            raise ValueError("Machine package differs from pinned binding.")
        return "illustrative" if package.definition.backend == "virtual" else "measured"

    def execute(self, plan: ProcessExecutionPlan, binding: StepBinding,
                submission: StepSubmission) -> AdapterResult:
        """Pin action/package identity and require feedback-derived completion."""
        target = binding.machine
        if target is None or target.machine_id != self.machine_id:
            return AdapterResult(status='unsupported', provenance='machine-measured', message='No matching machine binding.')
        package = self.client.capabilities().get('machine_package')
        if package is None:
            return AdapterResult(status='unsupported', provenance='machine-measured', message='Machine does not advertise an executable package.')
        from forma_core.control.package import MachinePackage
        package = MachinePackage.model_validate(package)
        if package.sha256 != target.package_sha256 or package.definition.machine_id != target.machine_id:
            raise ValueError('Machine package differs from the pinned binding.')
        if not any(a.action_id == target.action_id for a in package.actions):
            raise ValueError('Action is absent from the pinned machine package.')
        provenance = 'simulated' if package.definition.backend == 'virtual' else 'machine-measured'
        # Never automatically resend after an ambiguous network failure.
        if binding.step_id not in self.pending:
            self.pending[binding.step_id] = None
            reply = self.client.command('action', action_id=target.action_id, parameters=target.parameters)
            self.pending[binding.step_id] = reply['run_id']
        run_id = self.pending[binding.step_id]
        if run_id is None:
            return AdapterResult(status='unknown', provenance=provenance, message='Dispatch outcome unknown; reconcile with the machine before any retry.')
        receipt = self.client.action_result(run_id)
        if receipt.get('run_id') != run_id or receipt.get('package_sha256') != target.package_sha256 or receipt.get('action_id') != target.action_id or receipt.get('parameters') != target.parameters:
            raise ValueError('Machine receipt does not match the dispatched action.')
        action = next(a for a in package.actions if a.action_id == target.action_id)
        if receipt.get('feedback_source') != action.completion.source or receipt.get('adapter_id') != package.adapter.adapter_id or receipt.get('provenance') != package.adapter.evidence:
            raise ValueError('Machine receipt provenance differs from its pinned package.')
        simulated = receipt.get('feedback_source') == 'simulated'
        provenance = 'simulated' if simulated else 'machine-measured'
        complete = receipt.get('status') == 'completed'
        if complete and (receipt.get('feedback_source') not in {'simulated', 'measured'} or not receipt.get('evidence')):
            return AdapterResult(status='unknown', provenance=provenance, message='Missing machine feedback evidence.', machine_receipt=receipt)
        status = 'ready' if complete else 'unknown' if receipt.get('status') == 'running' else 'failed'
        return AdapterResult(status=status, provenance=provenance, message='Machine action status; not proof of chemical transformation.', machine_receipt=receipt)
