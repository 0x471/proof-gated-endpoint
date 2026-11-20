#!/usr/bin/env bash
# The FlashProve pilot on the Pi: exploratory, never pooled with registered runs. It sets the deadline, the
# timing slot, pacing and sample size for the confirmatory protocols. Each arm is one
# device/flashprove_scenario.sh run with a fresh random message, written to OUT/NAME.bits before the run
# (the honest arm's bits are sham labels the server never sees). D is the endpoint deadline in seconds.
#   pilot_honest          fixed, D 10, 64 sequential honest answers: check and cycle times, sham-label baseline
#   pilot_timing          fixed, D 10, 64 answers; the server waits 1 s before answering a 1 bit
#   pilot_timing_arrival  arrival release, the same encoder, 32 answers: positive control
#   pilot_order           fixed, D 20, 8 batches of 3 answers sent in a message-chosen order: reply order must hold
#   pilot_orderfail       fixed, D 7.5 (one check fits, two don't), 16 batches of 2: the order picks which fails
#   pilot_orderfail_wide  the same encoder at D 12 (both checks fit): negative control
#   pilot_straddle        fixed, D 10, one request every 7 s, 33 requests: answer k arrives 1 s before (1) or
#                         0.5 s after (0) request k-1's release
# Usage from the repo root: CONFINE=seccomp-only PY=... device/flashprove_pilot.sh  (OUT defaults to
# runs/fp_pilot_<UTC time>). Prints one line per arm; observer/pilot_summary.py OUT summarises.
set -uo pipefail
cd "$(dirname "$0")/.."
export OUT="${OUT:-runs/fp_pilot_$(date -u +%Y%m%dT%H%M%SZ)}"
export CONFINE="${CONFINE:-landlock}"
mkdir -p "$OUT"
fails=0
bits() { /usr/bin/python3 -I -c 'import secrets, sys; print("".join(secrets.choice("01") for _ in range(int(sys.argv[1]))))' "$1"; }
run() { device/flashprove_scenario.sh "$@" || { echo "FAIL $1"; fails=$((fails + 1)); }; }

b=$(bits 64); echo "$b" > "$OUT/pilot_honest.bits"
DEADLINE=10 run pilot_honest fixed 64 1 -- --mode honest
b=$(bits 64); echo "$b" > "$OUT/pilot_timing.bits"
DEADLINE=10 run pilot_timing fixed 64 1 -- --mode timing_bits --bits "$b" --slot 1
b=$(bits 32); echo "$b" > "$OUT/pilot_timing_arrival.bits"
DEADLINE=10 run pilot_timing_arrival arrival 32 1 -- --mode timing_bits --bits "$b" --slot 1
b=$(bits 16); echo "$b" > "$OUT/pilot_order.bits"
DEADLINE=20 run pilot_order fixed 24 3 -- --mode order_bits --batch 3 --bits "$b"
b=$(bits 16); echo "$b" > "$OUT/pilot_orderfail.bits"
DEADLINE=7.5 run pilot_orderfail fixed 32 2 -- --mode order_bits --batch 2 --bits "$b"
b=$(bits 16); echo "$b" > "$OUT/pilot_orderfail_wide.bits"
DEADLINE=12 run pilot_orderfail_wide fixed 32 2 -- --mode order_bits --batch 2 --bits "$b"
b=$(bits 32); echo "$b" > "$OUT/pilot_straddle.bits"
DEADLINE=10 PERIOD_MS=7000 run pilot_straddle fixed 33 1 -- --mode straddle_bits --bits "$b" --period 7 --lead 1 --shift 0.5

echo "pilot: $fails failed arms; files in $OUT"
[ "$fails" = 0 ]
