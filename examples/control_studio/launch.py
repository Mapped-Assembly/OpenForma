"""Start the optional studio on loopback, or open an already-running instance."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT=Path(__file__).resolve().parent
URL="http://127.0.0.1:8766"


def ready():
    try:
        with urlopen(URL+"/api/control/v2/machines",timeout=.5) as response:
            return any(m.get("machine_id")=="xy-stage" for m in json.load(response))
    except (OSError,ValueError):
        return False


if not ready():
    (ROOT/"generated").mkdir(exist_ok=True)
    with (ROOT/"generated/server.log").open("ab") as log:
        child=subprocess.Popen([sys.executable,str(ROOT/"server.py")],cwd=ROOT,stdout=log,stderr=log,
            creationflags=(subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform=="win32" else 0,
            start_new_session=sys.platform!="win32")
    for _ in range(40):
        if ready():break
        if child.poll() is not None:raise SystemExit("Studio failed to start; see generated/server.log.")
        time.sleep(.2)
    else:raise SystemExit("Studio did not become ready; see generated/server.log.")
webbrowser.open(URL)
print("Control Studio:",URL)
