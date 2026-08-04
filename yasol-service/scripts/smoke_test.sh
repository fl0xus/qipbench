#!/usr/bin/env bash
# End-to-end check against a running container. Usage: scripts/smoke_test.sh [base_url]
set -euo pipefail

BASE="${1:-http://localhost:8000}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== /health =="
curl -sf "$BASE/health"
echo

echo "== /examples =="
curl -sf "$BASE/examples"
echo

echo "== /solve/qlp (knapsack) =="
curl -sf "$BASE/examples/knapsack.qlp" | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin)["content"])' > /tmp/knapsack.qlp
curl -sf -X POST "$BASE/solve/qlp" \
    -F "file=@/tmp/knapsack.qlp" \
    -F "time_limit=60" \
  | python3 -c '
import json, sys
r = json.load(sys.stdin)
print("status          :", r["status"])
print("objective_value :", r["objective_value"])
print("variables       :", len(r["variables"]), "->", r["variables"][:3])
print("duration        :", r["duration_seconds"], "s   timed_out:", r["timed_out"])
assert r["status"] == "OPTIMAL", r["status"]
assert r["objective_value"] == 863.0, r["objective_value"]
'

echo "== /solve/julia (Moore & Bard) =="
python3 -c "
import json, sys
print(json.dumps({'code': open('$DIR/examples/moore_bard.jl').read(), 'timeout': 120}))
" > /tmp/julia_job.json
curl -sf -X POST "$BASE/solve/julia" \
    -H 'Content-Type: application/json' \
    -d @/tmp/julia_job.json \
  | python3 -c '
import json, sys
r = json.load(sys.stdin)
print("exit_code :", r["exit_code"], " timed_out:", r["timed_out"], " duration:", r["duration_seconds"], "s")
print("artifacts :", sorted(r["artifacts"]))
for s in r["solutions"]:
    print("solution  :", s["file_name"], s["status"], s["objective_value"])
    print("            ", [(v["name"], v["value"]) for v in s["variables"]])
print("stdout tail:")
print("\n".join(r["stdout"].splitlines()[-15:]))
assert r["exit_code"] == 0, r["stderr"][-2000:]
assert r["solutions"], "no solution file produced"
'

echo
echo "OK"
