"""Joint-limited position-only IK for the mounted SO-101."""

import mujoco
import numpy as np


class ArmKinematics:
    """Resolve the five arm joints and tool site of one mounted rover."""

    def __init__(self, model: mujoco.MjModel, prefix: str) -> None:
        """Index only this instance in the shared model."""
        self.model = model
        self.joints = [model.joint(f"{prefix}arm_{i}") for i in range(1, 6)]
        self.qids = np.array([j.qposadr[0] for j in self.joints])
        self.dofs = np.array([j.dofadr[0] for j in self.joints])
        self.limits = np.array([j.range for j in self.joints])
        self.tcp = model.site(prefix + "arm_tcp").id

    def solve(self, data: mujoco.MjData, target: np.ndarray, initial: np.ndarray) -> np.ndarray:
        """Solve a reachable tool position against joint limits in scratch data."""
        target = np.asarray(target)
        for seed in (initial, np.array([1.4, -0.7, 0.7, -0.5, 0]), np.array([-1.4, 0.7, -0.7, 0.5, 0])):
            q = seed.copy()
            for _ in range(250):
                data.qpos[self.qids] = q
                mujoco.mj_forward(self.model, data)
                error = target - data.site_xpos[self.tcp]
                if np.linalg.norm(error) < 0.003:
                    return q
                jac = np.zeros((3, self.model.nv))
                mujoco.mj_jacSite(self.model, data, jac, None, self.tcp)
                j = jac[:, self.dofs]
                dq = j.T @ np.linalg.solve(j @ j.T + 0.0002 * np.eye(3), error)
                q = np.clip(q + np.clip(dq, -0.15, 0.15), self.limits[:, 0], self.limits[:, 1])
        raise ValueError(f"Tool target unreachable: {target.tolist()}")
