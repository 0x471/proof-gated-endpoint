#!/usr/bin/env bash
# What cp_verify does with FlashProve's stored proof and with changed copies of it, on this machine:
# exit code, standard output and error, and time of every call. The reader's tests use these outputs.
# Usage: device/flashprove_contract.sh CP_VERIFY FIXTURE OUT
#   FIXTURE is FlashProve's examples/accepted-proof. On the Pi:
#   ssh p01 bash -s -- p01-fp/cp_verify p01-fp/accepted-proof fp-contract < device/flashprove_contract.sh
# Also runs the call under the endpoint's per-process limits (stdout through a pipe, as the endpoint reads
# it), traces it with strace if installed, and measures the whole tree's peak memory in a cgroup if sudo
# works without a password. Writes OUT/summary.tsv, OUT/info.txt and OUT/res/*.
set -u
V=$(readlink -f "$1") EX=$(readlink -f "$2") OUT=$3
ID=$(tr -d '\n' < "$EX/ID.txt")
[[ "$ID" =~ ^[0-9a-f]{64}$ ]] || { echo "bad ID.txt"; exit 2; }
rm -rf "$OUT" && mkdir -p "$OUT/in" "$OUT/res" && cd "$OUT" || exit 2
OUT=$PWD
{
  echo "== machine"; uname -a; grep -E '^(PRETTY_NAME|VERSION)=' /etc/os-release; ldd --version | head -1; nproc
  grep -m1 -E 'Model|model name' /proc/cpuinfo; free -m | head -2
  cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | sort | uniq -c
  vcgencmd measure_temp 2>/dev/null; vcgencmd get_throttled 2>/dev/null
  sha256sum "$V"; echo "id $ID"
} > info.txt 2>&1

cp "$EX/example.req" "$EX/example.y" "$EX/example.proof" in/ || exit 2
python3 -I - "$OUT/in" <<'PY' || { echo "could not write the changed inputs"; exit 2; }
import struct, sys
d = sys.argv[1]
req = open(f"{d}/example.req", "rb").read()
y = open(f"{d}/example.y", "rb").read()
proof = open(f"{d}/example.proof", "rb").read()
get = lambda b, i: struct.unpack_from("<I", b, 4 * i)[0]
def word(b, i, v):
    b = bytearray(b); b[4 * i:4 * i + 4] = struct.pack("<I", v); return bytes(b)
def w(name, data): open(f"{d}/{name}", "wb").write(data)
w("req_prompt0", word(req, 0, (get(req, 0) + 1) % 512))
w("req_prompt60", word(req, 60, (get(req, 60) + 1) % 512))
w("req_cont61", word(req, 61, (get(req, 61) + 1) % 512))
w("req_cont63", word(req, 63, (get(req, 63) + 1) % 512))
w("req_e63", word(req, 65, 63))
w("req_r59", word(req, 64, 59))
w("y_200", struct.pack("<I", (get(y, 0) + 1) % 512))
n = len(proof)
for name, off in [("proof_flip0", 0), ("proof_flipmid", n // 2), ("proof_flipend", n - 1)]:
    p = bytearray(proof); p[off] ^= 1; w(name, bytes(p))
w("proof_trunc1", proof[:-1])
w("proof_half", proof[:n // 2])
w("proof_empty", b"")
w("proof_append1", proof + b"\0")
PY
for part in key leaf kl; do cp -r "$EX/config" "in/config_$part" && cp -r "$EX/ivk" "in/ivk_$part" || exit 2; done
python3 -I - "$OUT/in" <<'PY' || { echo "could not write the changed bundle files"; exit 2; }
import sys
d = sys.argv[1]
for path in ("ivk_key/key.bin", "ivk_leaf/leaf/0.bin", "config_kl/KL.bin"):
    f = f"{d}/{path}"; b = bytearray(open(f, "rb").read()); b[len(b) // 2] ^= 1; open(f, "wb").write(bytes(b))
PY

BADID=${ID%?}$([ "${ID: -1}" = 0 ] && echo 1 || echo 0)
printf "case\trep\texit\tseconds\tstdout_bytes\tstderr_bytes\tlast_line\n" > summary.tsv
# Standard output goes through a pipe, as in the endpoint: a file would meet the file-size limit.
run() {
  local name=$1 reps=$2 i s e code; shift 2
  for i in $(seq 1 "$reps"); do
    s=$(date +%s%N)
    "$@" 2> "res/$name.$i.err" | cat > "res/$name.$i.out"
    code=${PIPESTATUS[0]}
    e=$(date +%s%N)
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$name" "$i" "$code" "$(awk -v a="$s" -v b="$e" 'BEGIN { printf "%.3f", (b - a) / 1e9 }')" \
      "$(wc -c < "res/$name.$i.out")" "$(wc -c < "res/$name.$i.err")" "$(tail -n 1 "res/$name.$i.out")" >> summary.tsv
  done
}
C=$EX/config K=$EX/ivk R=in/example.req Y=in/example.y P=in/example.proof
LIMITS=(env -i prlimit --as=268435456 --fsize=0 --nofile=64 --core=0 --cpu=60)
run accept 10 "$V" $C $K $R $Y $P -id "$ID" -q
run accept_limits 5 "${LIMITS[@]}" "$V" $C $K $R $Y $P -id "$ID" -q
run accept_j4_limits 3 "${LIMITS[@]}" "$V" $C $K $R $Y $P -id "$ID" -q -j 4
for r in req_prompt0 req_prompt60 req_cont61 req_cont63 req_r59 req_e63; do run "$r" 3 "$V" $C $K "in/$r" $Y $P -id "$ID" -q; done
run y_200 3 "$V" $C $K $R in/y_200 $P -id "$ID" -q
run id_changed 3 "$V" $C $K $R $Y $P -id "$BADID" -q
run no_id 1 "$V" $C $K $R $Y $P -q
for p in proof_flip0 proof_flipmid proof_flipend proof_trunc1 proof_half proof_empty proof_append1; do
  run "$p" 3 "$V" $C $K $R $Y "in/$p" -id "$ID" -q
done
run key_changed 3 "$V" in/config_key in/ivk_key $R $Y $P -id "$ID" -q
run leaf_changed 3 "$V" in/config_leaf in/ivk_leaf $R $Y $P -id "$ID" -q
run kl_changed 3 "$V" in/config_kl in/ivk_kl $R $Y $P -id "$ID" -q
run as_128mib 1 env -i prlimit --as=134217728 "$V" $C $K $R $Y $P -id "$ID" -q

for i in $(seq 2 10); do
  diff <(tail -n 4 res/accept.1.out) <(tail -n 4 "res/accept.$i.out") > /dev/null || echo "accept run $i closing lines differ" >> info.txt
done
if command -v strace > /dev/null; then
  strace -f -qq -o strace.txt "$V" $C $K $R $Y $P -id "$ID" -q > /dev/null 2>&1
  strace -f -c -o strace_counts.txt "$V" $C $K $R $Y $P -id "$ID" -q > /dev/null 2>&1
fi
if sudo -n true 2> /dev/null; then
  sudo -n systemd-run --quiet --scope -p MemoryAccounting=yes -- bash -c \
    "$V $C $K $R $Y $P -id $ID -q > /dev/null; cat /sys/fs/cgroup\$(cut -d: -f3 /proc/self/cgroup)/memory.peak" > memory_peak.txt 2>> info.txt
fi
{ vcgencmd measure_temp 2>/dev/null; vcgencmd get_throttled 2>/dev/null; echo done; } >> info.txt
awk -F'\t' 'NR > 1 { k = $1 "\t" $3 "\t" $7; n[k]++ } END { for (k in n) print n[k] "\t" k }' summary.tsv | sort -k2
