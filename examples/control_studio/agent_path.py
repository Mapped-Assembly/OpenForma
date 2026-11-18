"""Exercise the running shared API with an agent-compatible client."""
import argparse
import json
from pathlib import Path
from forma_core.control import ControlClient

parser=argparse.ArgumentParser()
parser.add_argument("--url",default="http://127.0.0.1:8766")
parser.add_argument("--takeover",action="store_true")
args=parser.parse_args()
client=ControlClient(args.url,"xy-stage")
results=[]
try:
    client.claim("XY agent benchmark",takeover=args.takeover)
    client.command("arm")
    for target in ({"x":20,"y":15},{"x":-20,"y":15},{"x":-20,"y":-15},{"x":20,"y":-15},{"x":0,"y":0}):
        result=client.move_and_wait(target)
        assert all(result["axes"][a]["simulated"]==v and result["axes"][a]["measured"] is None for a,v in target.items())
        results.append({"targets":target,"state":result})
finally:
    client.release()
report={"passed":True,"scope":"Live HTTP API and virtual adapter; no autonomous LLM or physical hardware.","segments":results}
out=Path(__file__).resolve().parent/"generated/live-agent-benchmark.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
print("Live two-axis agent-client path passed:",len(results),"segments; released and disarmed.")
