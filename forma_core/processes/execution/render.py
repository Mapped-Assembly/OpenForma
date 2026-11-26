"""Render pinned CAD meshes using OpenForma feedback and virtual inventory traces.

The viewer never schedules actions, interpolates unobserved poses, or modifies
process state. Pillow and NumPy are optional rendering dependencies.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from forma_core.domains.base import fingerprint
from .workcell import VirtualWorkcell


def body_transforms(spec: VirtualWorkcell, positions: dict[str, float | None]) -> dict[str, Any]:
    """Resolve the existing MachineDefinition joint hierarchy using simulated feedback."""
    import numpy as np
    axes = {a.axis_id: a for a in spec.machine.definition.axes}
    transforms: dict[str, Any] = {}

    def resolve(axis_id: str) -> Any:
        """Compose rotation/translation about each declared world rest origin."""
        if axis_id in transforms:
            return transforms[axis_id]
        joint = axes[axis_id].joint
        if joint is None:
            raise ValueError('Renderable axes require joint bindings.')
        value = positions.get(axis_id)
        if value is None or not math.isfinite(value):
            raise ValueError('Cannot render missing or nonfinite machine feedback.')
        value *= joint.sign
        matrix = np.eye(4)
        direction = np.array(joint.axis)
        if joint.kind == 'prismatic':
            matrix[:3, 3] = direction * value
        else:
            angle = math.radians(value)
            x, y, z = direction
            cross = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
            rotation = np.eye(3) + math.sin(angle)*cross + (1-math.cos(angle))*(cross@cross)
            matrix[:3, :3] = rotation
            origin = np.array(joint.origin_mm)
            matrix[:3, 3] = origin - rotation@origin
        transforms[axis_id] = resolve(joint.parent_axis)@matrix if joint.parent_axis else matrix
        return transforms[axis_id]

    return {body: resolve(axis.axis_id) for axis in axes.values() if axis.joint for body in axis.joint.body_ids}


def rasterize(polygons: list[tuple[float, Any, tuple[int, int, int]]], width: int, height: int) -> Any:
    """Depth-test projected triangles; average-depth painter sorting hides moving parts incorrectly."""
    import numpy as np
    pixels = np.empty((height, width, 3), dtype=np.uint8)
    pixels[:] = (16, 33, 43)
    depth = np.full((height, width), -np.inf, dtype=np.float32)
    for _, polygon, color in polygons:
        for i in range(1, len(polygon)-1):
            points = polygon[[0, i, i+1]]
            low = np.maximum(np.floor(points[:, :2].min(axis=0)).astype(int), [0, 148])
            high = np.minimum(np.ceil(points[:, :2].max(axis=0)).astype(int), [width-1, 610])
            x0, y0 = low; x1, y1 = high
            if x0 > x1 or y0 > y1:
                continue
            a, b, c = points
            denominator = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(denominator) < 1e-9:
                continue
            yy, xx = np.ogrid[y0:y1+1, x0:x1+1]
            w0 = ((b[1]-c[1])*(xx+.5-c[0])+(c[0]-b[0])*(yy+.5-c[1]))/denominator
            w1 = ((c[1]-a[1])*(xx+.5-c[0])+(a[0]-c[0])*(yy+.5-c[1]))/denominator
            w2 = 1-w0-w1
            z = w0*a[2]+w1*b[2]+w2*c[2]
            region = depth[y0:y1+1, x0:x1+1]
            mask = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & (z > region)
            region[mask] = z[mask]
            pixels[y0:y1+1, x0:x1+1][mask] = color
    return pixels


def render_workcell(request: dict[str, Any], trace: dict[str, Any], scene: dict[str, Any], output: Path) -> None:
    """Export an animated GIF from verified snapshots and the existing mesh format."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    spec = VirtualWorkcell.model_validate(request['workcell'])
    payload = {k: v for k, v in trace.items() if k != 'trace_sha256'}
    if fingerprint(payload) != trace.get('trace_sha256') or fingerprint(spec) != trace['workcell_sha256']:
        raise ValueError('Trace or workcell hash mismatch.')
    if fingerprint(request['plan']) != trace['runtime']['plan_sha256']:
        # Normalize optional defaults exactly as the runtime does.
        from .models import ProcessExecutionPlan
        if fingerprint(ProcessExecutionPlan.model_validate(request['plan'])) != trace['runtime']['plan_sha256']:
            raise ValueError('Process plan hash mismatch.')
    if fingerprint(scene) != spec.geometry_sha256 or trace['geometry_sha256'] != spec.geometry_sha256:
        raise ValueError('Geometry hash mismatch.')
    if not trace['frames']:
        raise ValueError('No recorded workcell frames.')
    bodies = {b['id']: b for b in scene['bodies']}
    if len(bodies) != len(scene['bodies']):
        raise ValueError('Duplicate geometry body identity.')
    required = {b for a in spec.machine.definition.axes if a.joint for b in a.joint.body_ids}
    if required - bodies.keys() or {c.body_id for c in spec.containers} - bodies.keys():
        raise ValueError('Unresolved mesh body binding.')
    direction = np.array([.8, -1., .85]); direction /= np.linalg.norm(direction)
    right = np.cross([0., 0., 1.], direction); right /= np.linalg.norm(right)
    up = np.cross(direction, right)
    basis = np.array([right, up, direction])
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    def font(size: int) -> Any:
        """Use a scalable font when installed, otherwise Pillow's portable default."""
        return ImageFont.truetype(font_path, size) if Path(font_path).exists() else ImageFont.load_default()
    large, medium, small = font(25), font(19), font(15)
    meshes = [(b, np.array(b['positions']).reshape(-1, 3), np.array(b['indices'], dtype=int).reshape(-1, 3)) for b in bodies.values() if b['positions']]
    phases = {p.phase_id: p for p in spec.phases}
    containers = {c.container_id: c for c in spec.containers}
    identity = np.eye(4)
    images = []
    for frame in trace['frames']:
        transforms = body_transforms(spec, frame['positions'])
        polygons: list[tuple[float, Any, tuple[int, int, int]]] = []
        def project(points: Any) -> Any:
            """Fixed orthographic projection preserves comparable poses between frames."""
            view = np.asarray(points)@basis.T
            return np.column_stack((590+view[:, 0]*.8, 410-view[:, 1]*.8, view[:, 2]))
        def world(points: Any, body: str) -> Any:
            """Map native rest vertices through the sampled machine transform."""
            matrix = transforms.get(body, identity)
            return np.asarray(points)@matrix[:3, :3].T + matrix[:3, 3]
        for body, vertices, indices in meshes:
            points = world(vertices, body['id'])
            projected = project(points)
            triangles = points[indices]
            normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
            lengths = np.linalg.norm(normals, axis=1)
            illumination = .48 + .42*np.abs(normals@direction / np.maximum(lengths, 1e-10))
            for ids, light in zip(indices, illumination):
                poly = projected[ids]
                color = tuple(int(min(255, max(0, c*255*light))) for c in body['color'])
                polygons.append((float(poly[:, 2].mean()), poly, color))
        # Material surfaces represent inventory fractions, not fluid dynamics/density.
        for cid, container in containers.items():
            mass = frame['materials']['containers'][cid]['mass_g']
            if mass <= 0:
                continue
            center = np.array(container.center_mm)
            center[2] += container.height_mm*min(1., mass/container.capacity_g)
            angles = np.linspace(0, 2*math.pi, 40, endpoint=False)
            surface = np.column_stack((center[0]+container.radius_mm*np.cos(angles),
                                       center[1]+container.radius_mm*np.sin(angles), np.full(40, center[2])))
            poly = project(world(surface, container.body_id))
            polygons.append((float(poly[:, 2].mean()), poly, tuple(int(c*255) for c in container.color)))
        image = Image.new('RGB', (1200, 800), '#10212b')
        draw = ImageDraw.Draw(image)
        draw.text((28, 18), f'OpenForma | {spec.machine.definition.name}', font=large, fill='#f1f6fa')
        draw.text((28, 55), 'VIRTUAL MANIPULATION  /  runtime feedback drives equipment poses', font=small, fill='#9dbccd')
        image = Image.fromarray(rasterize(polygons, 1200, 800))
        draw = ImageDraw.Draw(image)
        draw.text((28, 18), f'OpenForma | {spec.machine.definition.name}', font=large, fill='#f1f6fa')
        draw.text((28, 55), 'VIRTUAL MANIPULATION  /  runtime feedback drives equipment poses', font=small, fill='#9dbccd')
        phase = phases.get(frame['phase_id'])
        if phase and phase.transfer and frame['machine_action'] and frame['machine_action']['status'] == 'running':
            a, b = containers[phase.transfer.source], containers[phase.transfer.target]
            endpoints = [project(world([c.center_mm], c.body_id))[0, :2] for c in (a, b)]
            for fraction in np.linspace(0, 1, 16):
                p = endpoints[0]*(1-fraction)+endpoints[1]*fraction
                draw.ellipse((p[0]-3, p[1]-3, p[0]+3, p[1]+3), fill='#63d7ef')
        draw.rounded_rectangle((22, 90, 1178, 145), 10, fill='#213b4a')
        draw.text((38, 105), f"{frame['time_s']:05.1f}s   {frame['phase_label']}", font=medium, fill='white')
        x = 30
        for step, status in frame['steps'].items():
            color = '#72e3b7' if status == 'completed' else '#ffd16e' if status == 'running' else '#78909e'
            draw.text((x, 620), step.upper(), font=small, fill=color)
            x += 188
        for i, (cid, container) in enumerate(containers.items()):
            amount = frame['materials']['containers'][cid]['mass_g']
            draw.text((30+i*380, 660), f'{container.label}: {amount:.2f} g', font=medium, fill='#d4e7ee')
        draw.text((30, 701), f"Virtual material residual: {frame['materials']['residual_g']:.6f} g   |   Process: {frame['process_status']}", font=small, fill='#72e3b7')
        draw.text((30, 734), 'Nominal inventory display; no liquid/contact solver. Agitation does not prove mixing or reaction.', font=small, fill='#a9bfcc')
        draw.text((30, 761), 'Physical experiment NOT RUN. Manipulation paths are virtual; actuators are not designed.', font=small, fill='#ffd16e')
        images.append(image)
    durations = [max(20, round((b['time_s']-a['time_s'])*1000)) for a, b in zip(trace['frames'], trace['frames'][1:])] + [2500]
    output.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(output, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=False)
    images[len(images)//2].save(output.with_suffix('.png'))


def main() -> None:
    """Render existing trace evidence without executing any machine or process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('trace', type=Path)
    parser.add_argument('scene', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    render_workcell(json.loads(args.request.read_text()), json.loads(args.trace.read_text()),
                    json.loads(args.scene.read_text()), args.output)


if __name__ == '__main__':
    main()
