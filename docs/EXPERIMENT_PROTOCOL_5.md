# Experiment protocol 8: the frozen image's timing (written 4 Nov, before any run)

The freeze image changed the kernel after the earlier image measurements: a minimal configuration without module support, built from `image/kernel.config`, and a boot that waits for the root partitions by partition id. The release code and its policy are unchanged, but the kernel decides when the endpoint wakes, so the earlier image results (Measurements) don't carry over to it by argument.

**Question.** On the frozen image, as it boots (fixed release, 4 s, 5 ms spin, from its signed store), does answer time still carry the server's planted bits, for the `timing_bits` encoder with 1 s slots and one sequential client? This repeats protocol 1's design, whose image run on 30 Oct found nothing at n = 512.

**Setup.**
- The freeze image, unchanged: the command line as built, the store's policy, the CPU speed policy as built (the kernel's default, `powersave`). Its sha256 goes into each manifest and into a dated note below before the first run.
- Runs: `timing_frozen` (server `--mode timing_bits --bits B --slot 1.0`, N = 512) then `honest_frozen` (server `--mode honest`, N = 512, analysed with the same labels), one boot, through `device/image_scenario.sh` with `LABEL=frozen`. One client. Laptop idle, both VMs stopped.
- Bits: 512 from `random.Random(20261104)`, each `rng.choice('01')`.
- One capture on the laptop's interface for both runs (`tcpdump -w`, port 8700), started before the first run and stopped after the second.
- A run counts only if its manifest says `"status": "ok"`, every answer is ok and the server reports no late sends; otherwise it is repeated in full, never patched.

**Primary analysis.** The held-out threshold decoder (`observer/jitter_test.py --heldout 2000`) on the timing run with the honest run as `--control`. Decision rule as in protocol 1: residue shown if the timing run has p < 0.01 and the control p > 0.05; not detected if the timing run has p > 0.05; otherwise inconclusive.

**How the outcome will be read** (fixed now):
- Not detected: the new kernel shows no residue under this decoder at n = 512, like the 30 Oct image; the frozen image's timing result is this run. It is not a bound.
- Shown: the kernel change brought a trace back; the freeze waits until its cause is found or the change is reverted.
- Inconclusive: reported as it is; the freeze goes ahead and the result is stated as inconclusive.

**Secondary** (reported, never used for the decision): mean difference with its permutation p (`--perms 20000`); mutual information with 4 bins (`observer/estimate.py`); `observer/shift_bound.py` with `--per-hour 900`; `observer/pcap_trace.py` on the capture (packet sizes, flags, TTL and window values).

**Limits fixed now.** One device, one boot, one client, one run per kind. The earlier image runs used other kernels and are never pooled with these.

## Commands
```sh
# In a second terminal before the first run (sudo), stopped with Ctrl-C after the second:
#   mkdir -p runs/p8; IF=$(route get 192.168.2.2 | awk '/interface/{print $2}'); sudo tcpdump -i $IF -w runs/p8/frozen.pcap tcp port 8700
B8=$(python3 -c "import random; r=random.Random(20261104); print(''.join(r.choice('01') for _ in range(512)))")
unset PERIOD_MS CLIENTS; export OUT=runs/p8 IMAGE_SHA=<sha256 of the flashed image> LABEL=frozen
device/image_scenario.sh timing_frozen 512 -- --mode timing_bits --bits $B8 --slot 1.0
device/image_scenario.sh honest_frozen 512 -- --mode honest
python3 observer/jitter_test.py runs/p8/timing_frozen.trace.jsonl $B8 --control runs/p8/honest_frozen.trace.jsonl --perms 20000 --heldout 2000
python3 observer/estimate.py runs/p8/timing_frozen.trace.jsonl $B8 --bins 4 --control runs/p8/honest_frozen.trace.jsonl
python3 observer/shift_bound.py runs/p8/timing_frozen.trace.jsonl $B8 --per-hour 900
python3 observer/pcap_trace.py runs/p8/frozen.pcap > runs/p8/frozen.pcap.summary.json
```
Each run takes about 35 minutes (512 answers at 4 s).

## Notes before the first run
