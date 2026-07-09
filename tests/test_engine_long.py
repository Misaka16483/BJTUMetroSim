"""测试仿真引擎 — 运行20秒观察列车加速出站."""
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

# Clear old DB
import sqlite3
db = sqlite3.connect("outputs/runs/phase1_engine.sqlite")
db.execute("DELETE FROM events")
db.execute("DELETE FROM metrics")
db.execute("DELETE FROM station_passenger_records")
db.execute("DELETE FROM train_load_records")
db.execute("DELETE FROM dwell_records")
db.execute("DELETE FROM dispatch_decisions")
db.execute("DELETE FROM runs")
db.commit()
db.close()

# Start
print(">>> Start simulation")
result = post("/api/sim/start")
print(f"    started at {result['simTimeMs']}ms")

# Poll for 20 seconds
for i in range(20):
    time.sleep(1)
    state = get("/api/sim/state")
    t = state["trains"][0]
    print(f"  t={state['clock']['tick']:3d}  {t['phase']:12s}  speed={t['speedMps']:6.2f}m/s  "
          f"station={t['currentStation']:6s}  next={t['nextStation']}  "
          f"pax={t['onboardPax']:3d}  dwell={t['dwellRemainingSec']:5.1f}s  "
          f"progress={t['segmentProgress']:.3f}")

# Stop
print("\n>>> Stop")
post("/api/sim/stop")

# Show recorded events count
db = sqlite3.connect("outputs/runs/phase1_engine.sqlite")
for table in ["events", "station_passenger_records", "dwell_records"]:
    count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table}: {count} rows")
db.close()
print("DONE")
