"""测试仿真引擎启动和状态查询."""
import json
import time
import urllib.request

def get(path):
    r = urllib.request.urlopen(f"http://127.0.0.1:8000{path}")
    return json.loads(r.read())

def post(path):
    req = urllib.request.Request(f"http://127.0.0.1:8000{path}", method="POST")
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

# Start sim
print(">>> POST /api/sim/start")
result = post("/api/sim/start")
print(json.dumps(result, indent=2))

# Wait 5 seconds
print("\n>>> Waiting 5 seconds...")
time.sleep(5)

# Get state  
print(">>> GET /api/sim/state")
state = get("/api/sim/state")
print("clock:", json.dumps(state["clock"], indent=2))
for t in state["trains"]:
    print(f"  Train {t['trainId']}: phase={t['phase']} speed={t['speedMps']}m/s "
          f"station={t['currentStation']} next={t['nextStation']} "
          f"pax={t['onboardPax']} progress={t['segmentProgress']}")

# Stop
print("\n>>> POST /api/sim/stop")
result = post("/api/sim/stop")
print(json.dumps(result, indent=2))
print("DONE")
