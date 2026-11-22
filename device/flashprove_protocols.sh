#!/usr/bin/env bash
# Protocols 9-11 on the Pi, as registered in docs/EXPERIMENT_PROTOCOL_6.md before any confirmatory data.
# Usage from the repo root: CONFINE=seccomp-only PY=... device/flashprove_protocols.sh 9|10|11
# OUT defaults to runs/p<N>_<UTC time>. Arms run in the registered order through device/flashprove_scenario.sh;
# each arm's bits come from its registered seed (Python's random.Random(seed), one choice of "0"/"1" per bit)
# and are written to OUT/NAME.bits before the arm starts. Honest arms get their paired code arm's bits as
# sham labels. ARMS="name ..." runs only those arms (to repeat a failed arm in full, into the same OUT); an
# earlier attempt's files are moved to OUT/attempt_<UTC time>_<random>/ first, never overwritten. Stops at the first
# failed arm.
set -uo pipefail
cd "$(dirname "$0")/.."
P="${1:-}"
[[ "$P" =~ ^(9|10|11)$ ]] || { echo "usage: device/flashprove_protocols.sh 9|10|11"; exit 2; }
export OUT="${OUT:-runs/p${P}_$(date -u +%Y%m%dT%H%M%SZ)}"
export CONFINE="${CONFINE:-landlock}"
bits() { /usr/bin/python3 -I -c 'import random, sys
r = random.Random(int(sys.argv[1])); n, skip = int(sys.argv[2]), int(sys.argv[3])
print("".join(r.choice("01") for _ in range(skip + n))[skip:])' "$@"; }

# name release N batch deadline period_ms cap max_pending seed nbits skip mode-args (BITS = the arm's bits)
case "$P" in
9) PLAN="p9_arrival arrival 64 1 10 - - 8 92 64 0 --mode timing_bits --bits BITS --slot 1
p9_T1 fixed 256 1 10 - - 8 91 256 0 --mode timing_bits --bits BITS --slot 1
p9_H1 fixed 256 1 10 - - 8 91 256 0 --mode honest
p9_H2 fixed 256 1 10 - - 8 91 256 256 --mode honest
p9_T2 fixed 256 1 10 - - 8 91 256 256 --mode timing_bits --bits BITS --slot 1" ;;
10) PLAN="p10_order fixed 192 3 20 - - 8 101 128 0 --mode order_bits --batch 3 --bits BITS
p10_orderfail fixed 256 2 7.5 - - 8 102 128 0 --mode order_bits --batch 2 --bits BITS
p10_orderfail_wide fixed 256 2 12 - - 8 103 128 0 --mode order_bits --batch 2 --bits BITS
p10_orderfail_cap fixed 256 2 7.5 - 8,2,8 8 104 128 0 --mode order_bits --batch 2 --bits BITS
p10_pending1 fixed 256 2 10 - - 1 105 128 0 --mode honest" ;;
11) PLAN="p11_S1 fixed 257 1 10 7000 - 8 111 256 0 --mode straddle_bits --bits BITS --period 7 --lead 1 --shift 0.5
p11_H1 fixed 257 1 10 7000 - 8 111 256 0 --mode honest
p11_H2 fixed 257 1 10 7000 - 8 111 256 256 --mode honest
p11_S2 fixed 257 1 10 7000 - 8 111 256 256 --mode straddle_bits --bits BITS --period 7 --lead 1 --shift 0.5" ;;
esac

if [[ -n "${ARMS:-}" ]]; then
  available=" "
  while read -r name rest; do available+="$name "; done <<< "$PLAN"
  selected=0
  selected_names=" "
  for name in $ARMS; do
    [[ "$available" == *" $name "* ]] || { echo "unknown arm for protocol $P: $name"; exit 2; }
    selected=$((selected + 1))
    selected_names+="$name "
  done
  [[ $selected -gt 0 ]] || { echo "ARMS selects no arms"; exit 2; }
  ARMS="$selected_names"
fi

mkdir -p "$OUT"
fails=0
while read -r name release n batch deadline period cap pending seed nbits skip args; do
  [[ -z "${ARMS:-}" || " $ARMS " == *" $name "* ]] || continue
  if compgen -G "$OUT/$name.*" >/dev/null; then
    old=$(mktemp -d "$OUT/attempt_$(date -u +%Y%m%dT%H%M%SZ)_XXXXXX") || exit 1
    mv "$OUT/$name".* "$old/" || exit 1
  fi
  b=$(bits "$seed" "$nbits" "$skip"); echo "$b" > "$OUT/$name.bits"
  export DEADLINE="$deadline" MAX_PENDING="$pending"
  if [ "$period" = - ]; then unset PERIOD_MS; else export PERIOD_MS="$period"; fi
  if [ "$cap" = - ]; then unset CAP; else export CAP="${cap//,/ }"; fi
  # shellcheck disable=SC2086
  device/flashprove_scenario.sh "$name" "$release" "$n" "$batch" -- ${args//BITS/$b} </dev/null \
    || { echo "FAIL $name: stopping; repeat it in full with ARMS=$name OUT=$OUT"; fails=1; break; }
done <<< "$PLAN"
echo "protocol $P: $([ $fails = 0 ] && echo "all arms ok" || echo "stopped at a failed arm"); files in $OUT"
[ "$fails" = 0 ]
