#!/bin/sh
# Runs on the Pi during a scenario with CPU_SAMPLE=1 (protocol 12): once a minute, the CPU list each running
# cp_verify and each pge-endpoint thread may use, appended to $1 until killed. Lines are
# "uptime_seconds kind pid_or_tid name cpus". Reads /proc only, once a minute, the same in every arm.
[ $# -eq 1 ] || { echo "usage: pi_cpu_sample.sh FILE" >&2; exit 2; }
while :; do
  up=$(cut -d' ' -f1 /proc/uptime)
  for p in $(pgrep -x pge-endpoint); do
    for t in /proc/"$p"/task/*; do
      printf '%s endpoint %s %s %s\n' "$up" "${t##*/}" "$(sed -n 's/^Name:[[:space:]]*//p' "$t/status" 2>/dev/null)" \
        "$(sed -n 's/^Cpus_allowed_list:[[:space:]]*//p' "$t/status" 2>/dev/null)"
    done
  done >> "$1"
  for p in $(pgrep -x cp_verify); do
    printf '%s cp_verify %s cp_verify %s\n' "$up" "$p" "$(sed -n 's/^Cpus_allowed_list:[[:space:]]*//p' /proc/"$p"/status 2>/dev/null)"
  done >> "$1"
  sleep 60
done
