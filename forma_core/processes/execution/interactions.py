"""Declarative kinematic interaction guards for virtual workcells.

This contract establishes motion provenance and geometric anchor constraints.
It does not solve friction, forces, mesh collisions or human biomechanics.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

import numpy as np
from pydantic import Field, model_validator

from forma_core.domains.base import DomainModel, Text, unique

Action = Literal['move', 'grasp', 'carry', 'release', 'use', 'actuate']


class Entity(DomainModel):
    """A geometry identity with explicit capabilities and an initial rigid pose."""

    body_id: Text
    kind: Literal['fixed', 'actor', 'movable', 'mechanism']
    actions: list[Action] = Field(default_factory=list)
    initial_pose_mm: list[list[float]]
    grip_mm: list[float] = Field(default_factory=lambda: [0., 0., 0.], min_length=3, max_length=3)
    contact_mm: list[list[float]] = Field(default_factory=list)
    initial_support: Text | None = None
    initial_retention: bool = False

    @model_validator(mode='after')
    def valid(self) -> Entity:
        """Reject nonrigid frames and inconsistent motion capabilities."""
        rigid(self.initial_pose_mm)
        unique(self.actions, 'entity action')
        if self.kind == 'fixed' and set(self.actions) & {'move', 'grasp', 'carry', 'release'}:
            raise ValueError('Fixed entities cannot advertise transport actions.')
        if self.kind != 'movable' and (self.initial_support or self.initial_retention):
            raise ValueError('Only movable entities may inherit support or retention motion.')
        if self.initial_retention and not self.initial_support:
            raise ValueError('Initial retention requires a checked contact surface.')
        if self.kind == 'actor' and 'move' not in self.actions:
            raise ValueError('Actors must explicitly advertise move.')
        if self.kind == 'movable' and not {'grasp', 'carry', 'release'} <= set(self.actions):
            raise ValueError('Movable entities require grasp, carry and release.')
        if any(len(p) != 3 or not np.isfinite(p).all() for p in self.contact_mm):
            raise ValueError('Contact anchors must be finite 3-vectors.')
        return self


class Surface(DomainModel):
    """A bounded horizontal supporting patch in its owner's local frame, in mm."""

    surface_id: Text
    body_id: Text
    bounds_xy_mm: list[float] = Field(min_length=4, max_length=4)
    height_mm: float
    normal_sign: Literal[-1, 1] = 1

    @model_validator(mode='after')
    def valid(self) -> Surface:
        """Require finite nonempty support bounds."""
        x0, y0, x1, y1 = self.bounds_xy_mm
        if x0 >= x1 or y0 >= y1:
            raise ValueError('Support patch bounds must have positive area.')
        return self


class Mechanism(DomainModel):
    """A scalar transmission driven by an existing declared input axis."""

    body_id: Text
    input_axis: Text
    ratio_mm_per_unit: float
    direction: list[float] = Field(min_length=3, max_length=3)
    input_zero: float = 0.
    travel_mm: list[float] = Field(min_length=2, max_length=2)
    control_body: Text

    @model_validator(mode='after')
    def valid(self) -> Mechanism:
        """Require a unit translation direction and an ordered travel interval."""
        if not np.isclose(np.linalg.norm(self.direction), 1.) or self.travel_mm[0] > self.travel_mm[1]:
            raise ValueError('Invalid transmission direction or travel.')
        return self


class InteractionCommand(DomainModel):
    """An explicit action; before/after placement determines its feedback boundary."""

    action: Literal['grasp', 'release', 'use', 'actuate']
    entity: Text
    actor: Text
    surface: Text | None = None

    @model_validator(mode='after')
    def valid(self) -> InteractionCommand:
        """A release must name its receiving support; other actions must not."""
        if (self.action == 'release') != (self.surface is not None):
            raise ValueError('Exactly release requires a support surface.')
        return self


class InteractionSpec(DomainModel):
    """Strict interaction section of the existing workcell sidecar."""

    entities: list[Entity] = Field(min_length=1)
    surfaces: list[Surface] = Field(default_factory=list)
    mechanisms: list[Mechanism] = Field(default_factory=list)
    contact_tolerance_mm: float = Field(default=3., gt=0, le=10)

    @model_validator(mode='after')
    def valid(self) -> InteractionSpec:
        """Resolve every identity and reject cycles in initial support chains."""
        unique([e.body_id for e in self.entities], 'interaction entity')
        unique([s.surface_id for s in self.surfaces], 'support surface')
        unique([m.body_id for m in self.mechanisms], 'mechanism body')
        entities = {e.body_id: e for e in self.entities}
        surfaces = {s.surface_id: s for s in self.surfaces}
        for s in self.surfaces:
            if s.body_id not in entities:
                raise ValueError('Unknown support owner.')
        for e in self.entities:
            if e.initial_support and e.initial_support not in surfaces:
                raise ValueError('Unknown initial support.')
            if e.kind == 'movable' and (not e.initial_support or not e.contact_mm):
                raise ValueError('Movable entities need initial support and contact anchors.')
            seen = {e.body_id}
            current = e
            while current.initial_support:
                owner = surfaces[current.initial_support].body_id
                if owner in seen:
                    raise ValueError('Cyclic support relationship.')
                seen.add(owner)
                current = entities[owner]
        for m in self.mechanisms:
            if m.body_id not in entities or entities[m.body_id].kind != 'mechanism' or m.control_body not in entities or entities[m.control_body].kind not in {'fixed', 'mechanism'}:
                raise ValueError('Unresolved mechanism or control.')
        if {e.body_id for e in self.entities if e.kind == 'mechanism'} != {m.body_id for m in self.mechanisms}:
            raise ValueError('Every mechanism needs exactly one transmission.')
        return self


def rigid(value: Any) -> np.ndarray:
    """Validate a finite SE(3) transform with translations in millimetres."""
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3], [0, 0, 0, 1]):
        raise ValueError('Expected finite affine 4x4 pose.')
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(rotation), 1):
        raise ValueError('Expected rigid pose without scale/reflection.')
    return matrix.copy()


class InteractionRuntime:
    """Own support/grasp state and derive all nonactor poses from their causes."""

    def __init__(self, spec: InteractionSpec) -> None:
        """Initialize supported states, validate anchors, and retain action evidence."""
        self.spec = spec.model_copy(deep=True)
        self.entities = {e.body_id: e for e in self.spec.entities}
        self.surfaces = {s.surface_id: s for s in self.spec.surfaces}
        self.poses = {e.body_id: rigid(e.initial_pose_mm) for e in self.spec.entities}
        self.parent: dict[str, tuple[str, np.ndarray, str]] = {}
        self.holders: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self.started: set[str] = set()
        self.finished: set[str] = set()
        self.active_drives: dict[str, str] = {}
        self.last_inputs: dict[str, float] = {}
        for e in self.spec.entities:
            if e.initial_support:
                self._support(e.body_id, e.initial_support)
                if e.initial_retention:
                    owner, offset, _ = self.parent[e.body_id]
                    self.parent[e.body_id] = (owner, offset, "retained")
                    self.holders[e.body_id] = owner

    def _point(self, body: str, point: list[float]) -> np.ndarray:
        """Transform a local contact anchor into workcell coordinates."""
        return (self.poses[body] @ np.r_[point, 1.])[:3]

    def _near(self, actor: str, target: str) -> None:
        """Require coincident declared grip/control anchors, not merely nearby origins."""
        a = self._point(actor, self.entities[actor].grip_mm)
        b = self._point(target, self.entities[target].grip_mm)
        if np.linalg.norm(a - b) > self.spec.contact_tolerance_mm:
            raise ValueError(f'Contact missing: {actor} -> {target}.')

    def _support(self, body: str, surface_id: str) -> None:
        """Check all bottom anchors against a bounded, upward-facing support patch."""
        surface = self.surfaces[surface_id]
        if surface.body_id == body:
            raise ValueError('An entity cannot support itself.')
        owner = surface.body_id
        seen = {body}
        while owner in self.parent:
            if owner in seen:
                raise ValueError('Cyclic support/attachment.')
            seen.add(owner)
            owner = self.parent[owner][0]
        if owner == body:
            raise ValueError('Cyclic support/attachment.')
        matrix = self.poses[surface.body_id]
        if (matrix[:3, :3] @ np.array([0., 0., surface.normal_sign]))[2] < .99:
            raise ValueError('Support patch is not upward-facing.')
        inverse = np.linalg.inv(matrix)
        x0, y0, x1, y1 = surface.bounds_xy_mm
        eps = self.spec.contact_tolerance_mm
        points = self.entities[body].contact_mm
        if not points:
            raise ValueError('Missing object support anchors.')
        for point in points:
            x, y, z, _ = inverse @ np.r_[self._point(body, point), 1.]
            if not (x0-eps <= x <= x1+eps and y0-eps <= y <= y1+eps and abs(z-surface.height_mm) <= eps):
                raise ValueError(f'Unsupported placement: {body} on {surface_id}.')
        self.parent[body] = (surface.body_id, inverse @ self.poses[body], 'support')

    def _command(self, command: InteractionCommand, phase: str) -> None:
        """Apply a declared interaction after capability and geometry checks."""
        body, actor = command.entity, command.actor
        if body not in self.entities or actor not in self.entities:
            raise ValueError('Unknown interaction entity or actor.')
        entity = self.entities[body]
        if command.action not in self.entities[actor].actions:
            raise ValueError(f'Actor {actor} does not provide {command.action}.')
        if (self.entities[actor].kind != 'actor' and (self.holders.get(body) != actor or actor not in self.holders)) or command.action not in entity.actions:
            raise ValueError(f'Action {command.action} is not available for {body}.')
        if command.action == 'grasp':
            if entity.kind != 'movable' or body in self.holders or actor in self.holders.values():
                raise ValueError('Invalid grasp or occupied hand.')
            self._near(actor, body)
            self.parent[body] = (actor, np.linalg.inv(self.poses[actor]) @ self.poses[body], 'grasp')
            self.holders[body] = actor
        elif command.action == 'release':
            if self.holders.get(body) != actor or command.surface not in self.surfaces:
                raise ValueError('Release requires ownership and a known support.')
            self._support(body, command.surface)
            del self.holders[body]
        elif command.action == 'use':
            self._near(actor, body)
        elif command.action == 'actuate':
            mechanism = next((m for m in self.spec.mechanisms if m.body_id == body), None)
            if mechanism is None:
                raise ValueError('Actuation requires a declared transmission.')
            self._near(actor, mechanism.control_body)
            self.active_drives[body] = actor
        self.events.append({'phase_id': phase, **command.model_dump(mode='json'), 'basis': 'simulated-anchor-kinematics'})

    def commands(self, commands: list[InteractionCommand], phase: str, *, after: bool = False) -> None:
        """Commit a boundary atomically and idempotently; failed placement cannot release."""
        completed = self.finished if after else self.started
        if phase in completed:
            return
        trial = deepcopy(self)
        if not after:
            trial.active_drives = {}
        for command in commands:
            trial._command(command, phase)
        (trial.finished if after else trial.started).add(phase)
        self.__dict__.update(trial.__dict__)

    def update(self, actor_poses: dict[str, Any], positions: dict[str, float | None]) -> None:
        """Resolve authoritative poses from actor feedback, support chains and transmissions."""
        trial = deepcopy(self)
        for body, entity in trial.entities.items():
            if entity.kind == 'actor':
                if body not in actor_poses:
                    raise ValueError(f'Missing actor feedback: {body}.')
                trial.poses[body] = rigid(actor_poses[body])
        for mechanism in trial.spec.mechanisms:
            value = positions.get(mechanism.input_axis)
            if value is None or not np.isfinite(value):
                raise ValueError('Missing mechanism input feedback.')
            distance = (value - mechanism.input_zero) * mechanism.ratio_mm_per_unit
            if not mechanism.travel_mm[0]-1e-6 <= distance <= mechanism.travel_mm[1]+1e-6:
                raise ValueError('Mechanism travel exceeded.')
            previous = trial.last_inputs.get(mechanism.body_id, mechanism.input_zero)
            if abs(value-previous) > 1e-8:
                actor = trial.active_drives.get(mechanism.body_id)
                if actor is None:
                    raise ValueError('Mechanism moved without actuation.')
                trial._near(actor, mechanism.control_body)
            matrix = rigid(trial.entities[mechanism.body_id].initial_pose_mm)
            matrix[:3, 3] += np.asarray(mechanism.direction) * distance
            trial.poses[mechanism.body_id] = matrix
            trial.last_inputs[mechanism.body_id] = float(value)
        resolved: set[str] = set()
        def resolve(body: str, ancestors: set[str]) -> np.ndarray:
            """Compose attachment chains while guarding dynamic cycles."""
            if body in ancestors:
                raise ValueError('Cyclic interaction graph.')
            if body not in resolved and body in trial.parent:
                owner, offset, _ = trial.parent[body]
                trial.poses[body] = resolve(owner, ancestors | {body}) @ offset
            resolved.add(body)
            return trial.poses[body]
        for body in trial.entities:
            resolve(body, set())
        # Supported contents may not silently remain glued to an inverted carrier.
        for body, (_, _, kind) in list(trial.parent.items()):
            if kind == 'support':
                surface_id = next((e['surface'] for e in reversed(trial.events) if e['entity'] == body and e['action'] == 'release'), trial.entities[body].initial_support)
                trial._support(body, surface_id)
        self.__dict__.update(trial.__dict__)

    def snapshot(self) -> dict[str, Any]:
        """Expose causal poses and action history, explicitly without dynamic claims."""
        return {'poses_mm': {key: value.tolist() for key, value in self.poses.items()},
                'relationships': {key: {'parent': value[0], 'kind': value[2]} for key, value in self.parent.items()},
                'events': deepcopy(self.events), 'verification': 'kinematic-anchors-only',
                'forces_friction_collision_biomechanics': 'not_verified'}
