"""Reproducible two-axis control benchmark using a virtual clock, not wall timing."""
import argparse
import json
from pathlib import Path
from forma_core.control import MachineDefinition, MachineRuntime, ControlError

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument("--output",type=Path,default=ROOT/"generated/control-benchmark.json")
args=parser.parse_args()
now=0.0
definition=MachineDefinition.model_validate_json((ROOT/"definitions/xy-stage.json").read_text())
runtime=MachineRuntime(definition,clock=lambda:now)
session=runtime.claim("deterministic XY benchmark",kind="agent")["session_id"]
seq=0


def send(op,**values):
    global seq
    seq+=1
    return runtime.command(dict(protocol_version=2,session_id=session,lease_id=runtime.lease(session)["lease_id"],seq=seq,op=op,**values))


trace=[]
maximum_step={a.axis_id:0.0 for a in definition.axes}
max_progress_error=0.0
send("arm")
path=[{"x":-30,"y":-20},{"x":30,"y":-20},{"x":30,"y":20},{"x":-30,"y":20},{"x":-30,"y":-20},{"x":0,"y":0}]
for index,target in enumerate(path):
    starts=dict(runtime.commanded)
    send("move",targets=target,coordinated=True)
    for tick in range(1000):
        before=dict(runtime.commanded)
        now+=.02
        if tick%10==0:
            send("heartbeat")
        runtime.tick()
        progress=[]
        for axis in definition.axes:
            a=axis.axis_id
            maximum_step[a]=max(maximum_step[a],abs(runtime.commanded[a]-before[a]))
            assert abs(runtime.commanded[a]-before[a])<=axis.max_rate*.02+1e-8
            assert axis.minimum<=runtime.commanded[a]<=axis.maximum
            if target[a]!=starts[a]:
                progress.append((runtime.commanded[a]-starts[a])/(target[a]-starts[a]))
        if len(progress)>1:
            max_progress_error=max(max_progress_error,max(progress)-min(progress))
        trace.append(dict(time_s=round(now,6),segment=index+1,positions=dict(runtime.commanded)))
        if not runtime.plans:
            break
    else:
        raise AssertionError("Coordinated path segment never finished")
    assert runtime.commanded==target
assert max_progress_error<1e-9
send("move",targets={"x":40},rates={"x":10})
for _ in range(10):
    now+=.02;runtime.tick()
send("move",targets={"y":30})
for _ in range(10):
    now+=.02;runtime.tick()
assert abs(runtime.commanded["x"]-4)<1e-6 and abs(runtime.commanded["y"]-6)<1e-6
before=dict(runtime.targets)
try:
    send("move",targets={"x":-40,"y":31},coordinated=True)
except ControlError as error:
    assert error.reason=="position_limit"
else:
    raise AssertionError("Invalid target accepted")
assert runtime.targets==before
now+=1.1;runtime.tick()
assert runtime.state=="LINK_LOST"
held=dict(runtime.commanded)
send("heartbeat")
assert runtime.state=="LINK_LOST"
send("arm")
assert runtime.targets==held
runtime.release(session)
report=dict(passed=True,benchmark="two-axis-xy-v1",backend="virtual",axes=["x","y"],units="mm",
    coordinated_segments=len(path),samples=len(trace),maximum_step_mm=maximum_step,
    maximum_normalized_coordination_error=max_progress_error,
    checks={"coordinated_rectangle_and_return":True,"axis_limits_and_rates":True,"independent_axis_commands":True,
            "atomic_rejection":True,"watchdog_and_explicit_resume":True,"release_disables":not runtime.enabled},
    scope="Deterministic software control/kinematics benchmark, not physical motion or wall-clock timing qualification.",trace=trace)
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k!="trace"},indent=2))
