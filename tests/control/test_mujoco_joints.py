"""Declarative scalar-joint physics, independent of any named robot."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from forma_core.control import MachineRuntime, ControlError
from forma_core.control.mujoco_joints import MujocoJointAdapter, MujocoJointConfiguration
from forma_core.control.package import MachinePackage

SCENE = '''<mujoco><option gravity="0 0 0" timestep="0.002"/><worldbody>
<body name="rotor"><joint name="turn" type="hinge" axis="0 0 1" range="-60 60" damping="0.1"/>
<geom type="box" size=".1 .02 .02" mass="1" contype="0" conaffinity="0"/></body>
<body name="carriage" pos="0 0 .5"><joint name="slide" type="slide" axis="1 0 0" range="-.05 .05" damping="2"/>
<geom type="sphere" size=".02" mass=".1" contype="0" conaffinity="0"/></body></worldbody>
<actuator><motor name="drive" joint="turn" ctrllimited="true" ctrlrange="-2 2"/>
<position name="linear" joint="slide" kp="100" kv="5" ctrllimited="true" ctrlrange="-.05 .05"/></actuator></mujoco>'''


def declaration(root: Path, scene: str = SCENE) -> dict:
    """Create a pinned fixture using arbitrary names and mixed units."""
    (root/'scene.xml').write_bytes(scene.encode('utf-8'))
    return dict(definition=dict(machine_id='mixed', name='Mixed', axes=[
        dict(axis_id='rotation',label='Rotation',unit='degree',minimum=-45.,maximum=45.,neutral=0.,max_rate=60.),
        dict(axis_id='travel',label='Travel',unit='mm',minimum=-40.,maximum=40.,neutral=0.,max_rate=50.)]),
        model='scene.xml',artifacts=[dict(path='scene.xml',role='mujoco-scene',sha256=hashlib.sha256(scene.encode()).hexdigest())],
        bindings=dict(rotation=dict(joint='turn',actuator='drive',control='pd_torque',kp=10.,kd=1.,effort_limit=2.),
                      travel=dict(joint='slide',actuator='linear',control='position')))


def package(adapter: MujocoJointAdapter) -> MachinePackage:
    """Require actual feedback convergence for a mixed-unit action."""
    return MachinePackage.model_validate(dict(definition=adapter.definition().model_dump(mode='json'),model_source='external',
        adapter=adapter.describe().model_dump(mode='json'),actions=[dict(action_id='move-pair',label='Move',handler='move',
        axes=['rotation','travel'],targets=dict(rotation=20.,travel=15.),rates=dict(rotation=40.,travel=30.),
        completion=dict(source='simulated',tolerance=.3,dwell_s=.1,timeout_s=5.))]))


@unittest.skipUnless(importlib.util.find_spec('mujoco'), 'Optional robot-sim extra required')
class DeclarativeJointTests(unittest.TestCase):
    """Exercise state, authority, integrity and actuator semantics."""
    def setUp(self) -> None:
        """Prepare an isolated declaration and shared deterministic clock."""
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root=Path(temp.name)
        self.raw=declaration(self.root); self.now=0.; self.adapter=self.make()
        self.runtime=MachineRuntime(self.adapter.definition(),self.adapter,clock=lambda:self.now,package=package(self.adapter))
        self.owner=self.runtime.claim('test',kind='agent'); self.seq=0

    def make(self) -> MujocoJointAdapter:
        """Instantiate current data without robot-specific code."""
        return MujocoJointAdapter(MujocoJointConfiguration.model_validate(self.raw),self.root,clock=lambda:self.now)

    def send(self, op: str, **values: object) -> dict:
        """Use ordinary lease-controlled runtime commands."""
        self.seq+=1; lease=self.runtime.lease(self.owner['session_id'])
        return self.runtime.command(dict(protocol_version=2,op=op,seq=self.seq,session_id=self.owner['session_id'],lease_id=lease['lease_id'],**values))

    def advance(self, steps: int) -> None:
        """Advance with heartbeats through the normal runtime."""
        for _ in range(steps):
            self.now+=.02; self.send('heartbeat'); self.runtime.tick()

    def test_feedback_gated_action(self) -> None:
        """Actual physics converges in both degrees and millimetres."""
        self.send('arm'); result=self.send('action',action_id='move-pair',parameters={})
        self.assertEqual(self.adapter.read().axes['rotation'].simulated,0)
        self.advance(150); record=self.runtime.action_execution.snapshot(result['run_id'])
        self.assertEqual(record['status'],'completed')
        for axis,target in [('rotation',20),('travel',15)]:
            self.assertAlmostEqual(self.adapter.read().axes[axis].simulated,target,delta=.3)
            self.assertIsNone(self.adapter.read().axes[axis].measured)
        self.assertEqual(record['configuration_sha256'],self.adapter.config.sha256)

    def test_disarm_pauses(self) -> None:
        """Disarm preserves achieved positions and freezes virtual time."""
        self.send('arm'); self.send('move',targets={'rotation':20.}); self.advance(10); self.send('disarm')
        pose=self.adapter.data.qpos.copy(); at=float(self.adapter.data.time); self.advance(10)
        self.assertEqual(float(self.adapter.data.time),at); self.assertTrue((self.adapter.data.qpos==pose).all())

    def test_bad_targets_atomic(self) -> None:
        """Invalid targets cannot partially change simulation commands."""
        before=dict(self.adapter._targets)
        for values in ({'rotation':10.,'travel':100.},{'rotation':float('nan'),'travel':0.}):
            with self.assertRaises(ValueError): self.adapter.apply(values,True)
        self.assertEqual(before,self.adapter._targets); self.assertEqual(float(self.adapter.data.time),0)

    def test_authority_and_limits(self) -> None:
        """Ownership and operational limits still gate generic execution."""
        with self.assertRaises(ControlError): self.runtime.command(dict(protocol_version=2,op='arm',session_id='other',lease_id='fake',seq=1))
        self.send('arm')
        with self.assertRaises(ControlError): self.send('move',targets={'travel':100.})
        self.assertEqual(self.runtime.targets['travel'],0)

    def test_hash_and_missing_dependency(self) -> None:
        """Unpinned native input cannot be silently substituted."""
        (self.root/'scene.xml').write_text(SCENE+' ')
        with self.assertRaisesRegex(ValueError,'hash mismatch'): self.make()
        self.raw=declaration(self.root,SCENE.replace('<worldbody>','<asset><mesh name="bad" file="missing.stl"/></asset><worldbody>',1))
        with self.assertRaisesRegex(ValueError,'not hash-pinned'): self.make()

    def test_missing_duplicate_joint(self) -> None:
        """Names must resolve uniquely, independently of runtime axis IDs."""
        self.raw['bindings']['rotation']['joint']='missing'
        with self.assertRaises((ValueError,KeyError)): self.make()
        self.raw['bindings']['rotation']['joint']='slide'
        with self.assertRaisesRegex(ValueError,'Duplicate joint'): self.make()

    def test_units_and_range(self) -> None:
        """Units and native joint limits are binding contracts."""
        self.raw['definition']['axes'][0]['unit']='mm'
        with self.assertRaisesRegex(ValueError,'units'): self.make()
        self.raw['definition']['axes'][0]['unit']='degree'; self.raw['definition']['axes'][0]['maximum']=90.
        with self.assertRaisesRegex(ValueError,'native joint range'): self.make()

    def test_actuator_type_and_mapping(self) -> None:
        """A torque motor cannot be misrepresented as a position servo."""
        self.raw['bindings']['rotation']['actuator']='linear'; self.raw['bindings']['travel']['actuator']='drive'
        with self.assertRaisesRegex(ValueError,'direct unit-gear'): self.make()
        self.raw=declaration(self.root); self.raw['bindings']['rotation']=dict(joint='turn',actuator='drive',control='position')
        with self.assertRaisesRegex(ValueError,'position servo'): self.make()

    def test_unbound_actuator(self) -> None:
        """Reject implicit actuation policies for leftover actuators."""
        del self.raw['bindings']['travel']; self.raw['definition']['axes'].pop()
        with self.assertRaisesRegex(ValueError,'Every model actuator'): self.make()

    def test_include_and_path_escape(self) -> None:
        """Unsupported native loading paths are refused before compilation."""
        self.raw=declaration(self.root,'<mujoco><include file="other.xml"/></mujoco>')
        with self.assertRaisesRegex(ValueError,'without includes'): self.make()
        self.raw=declaration(self.root,SCENE.replace('<worldbody>','<asset><mesh name="bad" file="../escape.stl"/></asset><worldbody>',1))
        with self.assertRaisesRegex(ValueError,'escapes package'): self.make()

    def test_effort_bounded_and_identity(self) -> None:
        """Effort is capped; changed PD gains invalidate old receipts/contracts."""
        self.adapter.apply({'rotation':45.,'travel':0.},True); self.assertLessEqual(abs(self.adapter.data.ctrl[0]),2.)
        old=package(self.adapter); self.raw['bindings']['rotation']['kp']=20.; changed=self.make()
        with self.assertRaisesRegex(ValueError,'contract differs'): MachineRuntime(changed.definition(),changed,package=old)

    def test_host_package_loading(self) -> None:
        """Existing Control Studio discovers the adapter from a pinned sidecar."""
        if not importlib.util.find_spec('fastapi'): self.skipTest('Optional control extra required')
        from examples.control_studio.server import create_app
        from forma_core.control import MachineRegistry
        config=self.root/'joints.json'; config.write_text(self.adapter.config.model_dump_json())
        raw=package(self.adapter).model_dump(mode='json'); raw['artifacts']=[dict(path='joints.json',role='mujoco-joints',sha256=hashlib.sha256(config.read_bytes()).hexdigest())]
        path=self.root/'machine.json'; path.write_text(json.dumps(raw)); registry=MachineRegistry(); app=create_app(registry,package_paths=[path])
        self.assertIsInstance(registry.get('mixed').adapter,MujocoJointAdapter); self.assertEqual(registry.get('mixed').state,'DISARMED')

        from fastapi.testclient import TestClient
        client = TestClient(app)
        self.addCleanup(client.close)
        claim = client.post('/api/control/v2/machines/mixed/claim', json={'name': 'reset-check'}).json()
        self.assertNotEqual(client.post('/api/control/v2/machines/mixed/simulation', json={'session_id': 'wrong', 'op': 'reset'}).status_code, 200)
        previous = registry.get('mixed').adapter
        response = client.post('/api/control/v2/machines/mixed/simulation', json={'session_id': claim['session_id'], 'op': 'reset'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNot(registry.get('mixed').adapter, previous)
        self.assertIsNone(registry.get('mixed').owner)
        self.assertEqual(registry.get('mixed').state, 'DISARMED')

    def test_disable_does_not_integrate_elapsed_time(self) -> None:
        """A disable call must not apply an old command for an additional step."""
        self.adapter.apply({'rotation': 30., 'travel': 20.}, True)
        self.now += .02
        self.adapter.apply({'rotation': None, 'travel': None}, False)
        self.assertEqual(float(self.adapter.data.time), 0.)
        self.assertTrue((self.adapter.data.ctrl == 0).all())
