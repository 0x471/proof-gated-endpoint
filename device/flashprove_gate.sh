#!/usr/bin/env bash
# The FlashProve integration gate on the Pi: short scenarios through device/flashprove_scenario.sh,
# each checked against what the binding requires (docs/FLASHPROVE_BINDING.md). Not a measurement.
# Usage from the repo root: device/flashprove_gate.sh   (OUT defaults to runs/fp_gate_<UTC time>)
# The stored proof must pass and be released no earlier than the deadline; a changed first or last
# reply token or a flipped proof bit must give unavailable; a deadline shorter than one check must give
# unavailable and leave no cp_verify running and no job directory; with RELEASE_CPU=0 no cp_verify may
# run on CPU 0; the failure code must read back with the cap off. Prints one line per check.
set -uo pipefail
cd "$(dirname "$0")/.."
export OUT="${OUT:-runs/fp_gate_$(date -u +%Y%m%dT%H%M%SZ)}"
export DEADLINE="${DEADLINE:-10}"
mkdir -p "$OUT"
fails=0
check() {
  local name=$1 rule=$2
  if /usr/bin/python3 -I - "$OUT/$name.trace.jsonl" "$DEADLINE" "$rule" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
deadline, rule = float(sys.argv[2]), sys.argv[3]
oks = [r["answer"]["ok"] for r in rows]
if rule == "pass":
    ys = {tuple(r["answer"]["y"]) for r in rows if r["answer"]["ok"]}
    good = all(oks) and len(ys) == 1 and all(r["elapsed_ms"] >= deadline * 1000 for r in rows)
elif rule == "refused":
    good = not any(oks) and all(r["answer"]["error"] == "unavailable" for r in rows)
elif rule.startswith("bits="):
    sys.path.insert(0, "observer")
    from client import decode_failure_position
    good = decode_failure_position(rows, 2) == rule[5:]
sys.exit(0 if good else 1)
PY
  then echo "PASS $name ($rule)"; else echo "FAIL $name ($rule)"; fails=$((fails + 1)); fi
}
scenario() { device/flashprove_scenario.sh "$@" || { echo "FAIL $1 (run)"; fails=$((fails + 1)); }; }

scenario fp_gate_honest fixed 4 1 -- --mode honest && check fp_gate_honest pass
for mode in tamper_output tamper_last flip_proof; do
  scenario "fp_gate_$mode" fixed 2 1 -- --mode "$mode" && check "fp_gate_$mode" refused
done
DEADLINE=2 scenario fp_gate_short fixed 2 1 -- --mode honest && DEADLINE=2 check fp_gate_short refused
sleep 2
left="$(ssh -n p01 'pgrep -x cp_verify; ls -d /dev/shm/pge-flashprove-*/job-* 2>/dev/null')"
if [[ -z "$left" ]]; then echo "PASS nothing left after a killed check"; else echo "FAIL left behind: $left"; fails=$((fails + 1)); fi
ssh -n p01 'for i in $(seq 300); do for p in $(pgrep -x cp_verify); do grep -h Cpus_allowed_list /proc/$p/status; done; sleep 0.1; done' > "$OUT/fp_gate_cpu.cpus" 2>/dev/null &
watcher=$!
RELEASE_CPU=0 scenario fp_gate_cpu fixed 2 1 -- --mode honest && check fp_gate_cpu pass
wait "$watcher"
if [[ -s "$OUT/fp_gate_cpu.cpus" ]] && ! awk '{print $2}' "$OUT/fp_gate_cpu.cpus" | grep -qE '(^|[,-])0([,-]|$)'; then
  echo "PASS cp_verify kept off CPU 0 ($(sort -u "$OUT/fp_gate_cpu.cpus" | tr '\n' ' '))"
else
  echo "FAIL cp_verify CPU placement ($(sort -u "$OUT/fp_gate_cpu.cpus" | tr '\n' ' '))"; fails=$((fails + 1))
fi
scenario fp_gate_fail fixed 8 1 -- --mode fail_position --batch 2 --bits 1011 && check fp_gate_fail bits=1011
echo "gate: $fails failed checks; files in $OUT"
[[ $fails == 0 ]]
