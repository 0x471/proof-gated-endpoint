#!/bin/sh
# Runs on the Pi during a scenario: one line every 10 s, "uptime_seconds temp_millidegrees_C", appended to $1
# until killed. Reads sysfs only, so it costs the measured device almost nothing and is the same in every arm.
# Throttling needs no sampling: get_throttled's flags stick until reboot and are read after the run.
[ $# -eq 1 ] || { echo "usage: pi_temp_log.sh FILE" >&2; exit 2; }
while :; do
  printf '%s %s\n' "$(cut -d' ' -f1 /proc/uptime)" "$(cat /sys/class/thermal/thermal_zone0/temp)" >> "$1"
  sleep 10
done
