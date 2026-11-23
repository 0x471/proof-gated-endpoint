#!/usr/bin/env bash
# One FlashProve scenario on the Pi (docs/FLASHPROVE_BINDING.md): the Rust endpoint with the FlashProve
# backend runs on the Pi; the replay server and the observer run on this machine.
# Usage from the repo root: device/flashprove_scenario.sh NAME RELEASE N BATCH -- prover/server.py args...
#   e.g. device/flashprove_scenario.sh fp_honest fixed 8 1 -- --mode honest
# Needs: ssh p01 on a stock Linux card with ~/p01-fp holding pge-endpoint (the dynamic aarch64 build, the
# same file as BIN), cp_verify, flashprove_small.pins and accepted-proof/ (FlashProve's
# examples/accepted-proof); FIXTURE (default ../images/flashprove-test/accepted-proof) is the same
# directory on this machine; BIN (default ../images/flashprove-test/bin/pge-endpoint) is the binary the
# Pi must run; PY (default .venv/bin/python) runs the server; this machine on 192.168.2.1, the Pi on 192.168.2.2.
# DEADLINE (default 10), SPIN_MS (default 5), CAP="W F C", RELEASE_CPU=N, MAX_PENDING (default 8) and
# CONFINE (landlock, the default, or seccomp-only for the stock Pi kernel, which has no Landlock) set
# the endpoint; the server always gets the same --deadline. PERIOD_MS=P sends one request every P ms without
# waiting (straddle_bits, load_bits), instead of one at a time or in BATCHes. The Pi's temperature is logged
# every 10 s (device/pi_temp_log.sh) into OUT/NAME.temp.log; with CPU_SAMPLE=1 the CPU lists of cp_verify and the
# endpoint's threads once a minute (device/pi_cpu_sample.sh) into OUT/NAME.cpu.log. The checkout must be clean; the manifest names its commit, the hashes of what the Pi ran,
# its CPU speed settings and its temperature and throttling before and after. A run is "ok" only if the
# client finished, every row arrived, both endpoint logs were copied back and the Pi didn't throttle.
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="${OUT:-runs}"
DEADLINE="${DEADLINE:-10}"
SPIN_MS="${SPIN_MS:-5}"
CAP="${CAP:-}"
RELEASE_CPU="${RELEASE_CPU:-}"
MAX_PENDING="${MAX_PENDING:-8}"
CONFINE="${CONFINE:-landlock}"
PERIOD_MS="${PERIOD_MS:-}"
CPU_SAMPLE="${CPU_SAMPLE:-}"
BIN="${BIN:-../images/flashprove-test/bin/pge-endpoint}"
PY="${PY:-.venv/bin/python}"
FIXTURE="${FIXTURE:-../images/flashprove-test/accepted-proof}"
PINS=device/flashprove_small.pins
NAME=$1 RELEASE=$2 N=$3 BATCH=$4; shift 5
[[ "$NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "bad NAME"; exit 2; }
[[ "$RELEASE" =~ ^(arrival|fixed)$ ]] || { echo "bad RELEASE"; exit 2; }
[[ "$N" =~ ^[1-9][0-9]{0,4}$ && "$BATCH" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "bad N or BATCH"; exit 2; }
num='^(0|[1-9][0-9]*)(\.[0-9]+)?$'
[[ "$DEADLINE" =~ $num && "$SPIN_MS" =~ $num ]] || { echo "bad DEADLINE or SPIN_MS"; exit 2; }
[[ -z "$CAP" || "$CAP" =~ ^[1-9][0-9]{0,3}\ [0-9]{1,4}\ [1-9][0-9]{0,3}$ ]] || { echo "bad CAP"; exit 2; }
[[ -z "$RELEASE_CPU" || "$RELEASE_CPU" =~ ^[0-9]$ ]] || { echo "bad RELEASE_CPU"; exit 2; }
[[ "$MAX_PENDING" =~ ^[1-9][0-9]{0,2}$ ]] || { echo "bad MAX_PENDING"; exit 2; }
[[ -z "$PERIOD_MS" || "$PERIOD_MS" =~ $num ]] || { echo "bad PERIOD_MS"; exit 2; }
[[ -z "$PERIOD_MS" || "$BATCH" == 1 ]] || { echo "PERIOD_MS and BATCH > 1 don't go together"; exit 2; }
[[ "$CONFINE" =~ ^(landlock|seccomp-only)$ ]] || { echo "bad CONFINE"; exit 2; }
[[ -z "$CPU_SAMPLE" || "$CPU_SAMPLE" == 1 ]] || { echo "bad CPU_SAMPLE"; exit 2; }
[[ -z "$(git status --porcelain)" ]] || { echo "$NAME: the checkout has changes; commit them first"; exit 2; }
[[ -f "$BIN" && -f "$FIXTURE/example.proof" ]] || { echo "$NAME: missing $BIN or $FIXTURE"; exit 1; }
COMMIT="$(git rev-parse HEAD)"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$OUT"
cleanup() {
  ssh -n p01 "pkill -x pge-endpoint; pkill -f '[p]i_temp_log.sh'; pkill -f '[p]i_cpu_sample.sh'; true"
  pkill -f "[p]rover/server.py --listen 192.168.2.1"; sleep 0.5; pkill -9 -f "[p]rover/server.py --listen 192.168.2.1"; true
}
cleanup
trap cleanup EXIT
here="$(shasum -a 256 "$BIN" | cut -d' ' -f1)"
pi_files="$(ssh -n p01 'cd ~/p01-fp && sha256sum pge-endpoint cp_verify flashprove_small.pins accepted-proof/example.proof accepted-proof/config/* accepted-proof/ivk/key.bin accepted-proof/ivk/leaf/*')" \
  || { echo "$NAME: could not hash the Pi's files"; exit 1; }
for pair in "$here  pge-endpoint" "$(shasum -a 256 "$PINS" | cut -d' ' -f1)  flashprove_small.pins" \
            "$(shasum -a 256 "$FIXTURE/example.proof" | cut -d' ' -f1)  accepted-proof/example.proof"; do
  [[ "$pi_files" == *"$pair"* ]] || { echo "$NAME: the Pi's ${pair#*  } differs from this checkout"; exit 1; }
done
pi_state() {
  ssh -n p01 'echo "governor $(cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | sort -u | tr "\n" " ")";
    echo "freq_khz $(cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq | tr "\n" " ")";
    echo "temp $(vcgencmd measure_temp 2>/dev/null)"; echo "throttled $(vcgencmd get_throttled 2>/dev/null)";
    echo "boot_id $(cat /proc/sys/kernel/random/boot_id)"; echo "uptime_s $(cut -d" " -f1 /proc/uptime)"'
}
BEFORE="$(pi_state)"
PLATFORM="$(ssh -n p01 'uname -a; grep PRETTY_NAME /etc/os-release; ldd --version | head -1; grep -m1 Model /proc/cpuinfo')"
ssh -n p01 "ss -ltn | grep -q ':8700 '" && { echo "$NAME: port 8700 still busy"; exit 1; }
"$PY" prover/server.py --listen 192.168.2.1:8701 --flashprove "$FIXTURE" --pins "$PINS" "$@" --deadline "$DEADLINE" \
  2>"$OUT/$NAME.server.log" &
for i in $(seq 120); do grep -q listening "$OUT/$NAME.server.log" && break; sleep 0.5; done
grep -q listening "$OUT/$NAME.server.log" || { echo "$NAME: server did not start"; exit 1; }
read -r CW CF CC <<< "${CAP:-}"
LAUNCH="/home/bob/p01-fp/pge-endpoint --listen 192.168.2.2:8700 --server 192.168.2.1:8701 --bundle /home/bob/p01-fp/accepted-proof --pins /home/bob/p01-fp/flashprove_small.pins --backend flashprove --verifier /home/bob/p01-fp/cp_verify --confine $CONFINE --deadline $DEADLINE --spin-ms $SPIN_MS --release $RELEASE --max-pending $MAX_PENDING --log /home/bob/endpoint_$NAME.log${CAP:+ --cap-window $CW --cap-failures $CF --cap-cooldown $CC}${RELEASE_CPU:+ --release-cpu $RELEASE_CPU}"
ssh -n p01 "rm -f ~/endpoint_$NAME.log ~/endpoint_$NAME.err; nohup $LAUNCH </dev/null >/home/bob/endpoint_$NAME.err 2>&1 &"
ok=0
for i in $(seq 40); do
  if ssh -n p01 "ss -ltn | grep -q '192.168.2.2:8700 '" && lsof -nP -iTCP:8701 -sTCP:ESTABLISHED 2>/dev/null | grep -q 192.168.2.2; then ok=1; break; fi
  sleep 0.25
done
[ $ok = 1 ] || { echo "$NAME: endpoint not up"; ssh -n p01 "cat ~/endpoint_$NAME.err" | tail -3; exit 1; }
PROMPT="$(/usr/bin/python3 -I -c 'import struct, sys; w = struct.unpack("<66I", open(sys.argv[1], "rb").read()); print(list(w[:61]))' "$FIXTURE/example.req")"
if [ "$BATCH" -gt 1 ]; then B="--batch $BATCH"; elif [ -n "$PERIOD_MS" ]; then B="--period-ms $PERIOD_MS"; else B=""; fi
ssh p01 "cat > ~/pi_temp_log.sh && chmod +x ~/pi_temp_log.sh" < device/pi_temp_log.sh
ssh -n p01 "rm -f ~/temp_$NAME.log; nohup ~/pi_temp_log.sh ~/temp_$NAME.log </dev/null >/dev/null 2>&1 &"
if [ -n "$CPU_SAMPLE" ]; then
  ssh p01 "cat > ~/pi_cpu_sample.sh && chmod +x ~/pi_cpu_sample.sh" < device/pi_cpu_sample.sh
  ssh -n p01 "rm -f ~/cpu_$NAME.log; nohup ~/pi_cpu_sample.sh ~/cpu_$NAME.log </dev/null >/dev/null 2>&1 &"
fi
limit=$(awk -v n="$N" -v d="$DEADLINE" 'BEGIN { printf "%d", n * (d + 8) + 120 }')
timeout "$limit" /usr/bin/python3 observer/client.py 192.168.2.2 8700 "$PROMPT" --n "$N" --tokens $B > "$OUT/$NAME.trace.jsonl" 2>"$OUT/$NAME.client.err"
rc=$?
rows=$(wc -l < "$OUT/$NAME.trace.jsonl" | tr -d ' ')
sleep 1
ssh -n p01 "pkill -f '[p]i_temp_log.sh'; true"
ssh -n p01 "cat ~/temp_$NAME.log" > "$OUT/$NAME.temp.log" 2>/dev/null || true
temps=$(wc -l < "$OUT/$NAME.temp.log" 2>/dev/null | tr -d ' ')
cpus=0
if [ -n "$CPU_SAMPLE" ]; then
  ssh -n p01 "pkill -f '[p]i_cpu_sample.sh'; true"
  ssh -n p01 "cat ~/cpu_$NAME.log" > "$OUT/$NAME.cpu.log" 2>/dev/null || true
  cpus=$(wc -l < "$OUT/$NAME.cpu.log" 2>/dev/null | tr -d ' ')
fi
AFTER="$(pi_state)"
logs=0
ssh -n p01 "cat ~/endpoint_$NAME.log" > "$OUT/$NAME.endpoint.jsonl" && [[ -s "$OUT/$NAME.endpoint.jsonl" ]] || logs=1
ssh -n p01 "cat ~/endpoint_$NAME.err" > "$OUT/$NAME.endpoint.err" || logs=1
status=ok
[[ $rc == 0 && $rows == "$N" && $logs == 0 ]] || status=failed
[[ "$AFTER" == *"throttled throttled=0x0"* ]] || status=failed
NAME="$NAME" RELEASE="$RELEASE" N="$N" BATCH="$BATCH" DEADLINE="$DEADLINE" SPIN_MS="$SPIN_MS" CAP="$CAP" \
RELEASE_CPU="$RELEASE_CPU" MAX_PENDING="$MAX_PENDING" CONFINE="$CONFINE" PERIOD_MS="$PERIOD_MS" TEMPS="${temps:-0}" CPUS="${cpus:-0}" CPU_SAMPLE="$CPU_SAMPLE" COMMIT="$COMMIT" HERE="$here" STARTED="$STARTED" RC="$rc" ROWS="$rows" \
LOGS="$logs" STATUS="$status" PI_FILES="$pi_files" BEFORE="$BEFORE" AFTER="$AFTER" PLATFORM="$PLATFORM" \
  /usr/bin/python3 -I -c '
import json, os, sys
e = os.environ
files = dict(reversed(l.split("  ", 1)) for l in e["PI_FILES"].splitlines() if "  " in l)
m = {"name": e["NAME"], "endpoint": "rust", "backend": "flashprove", "release": e["RELEASE"], "n": int(e["N"]),
     "batch": int(e["BATCH"]), "deadline": float(e["DEADLINE"]), "spin_ms": float(e["SPIN_MS"]), "cap": e["CAP"],
     "release_cpu": e["RELEASE_CPU"], "max_pending": int(e["MAX_PENDING"]), "confine": e["CONFINE"],
     "period_ms": e["PERIOD_MS"], "temp_samples": int(e["TEMPS"] or 0),
     "cpu_sample": e["CPU_SAMPLE"] == "1", "cpu_samples": int(e["CPUS"] or 0),
     "commit": e["COMMIT"],
     "endpoint_sha256": e["HERE"], "pi_sha256": files, "platform": e["PLATFORM"].splitlines(),
     "pi_before": e["BEFORE"].splitlines(), "pi_after": e["AFTER"].splitlines(), "started": e["STARTED"],
     "client_exit": int(e["RC"]), "rows": int(e["ROWS"]), "logs_copied": e["LOGS"] == "0",
     "status": e["STATUS"], "server_args": sys.argv[1:]}
print(json.dumps(m))' "$@" > "$OUT/$NAME.manifest.json" || status=failed
echo "$NAME: client exit $rc rows $rows/$N logs $([[ $logs == 0 ]] && echo copied || echo missing) $status"
[[ $status == ok ]]
