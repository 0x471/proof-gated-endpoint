# Experiment protocol 12: the load leak with an idle laptop, with and without a release CPU (written 23 Nov, before any run)

Registered after protocols 9-11 (`EXPERIMENT_PROTOCOL_6.md`) and before any protocol 12 data. Design from the second independent review of those runs. Its numbers come only from `book/src/results.md`, "Load near a release (21 Nov, protocol 11)".

**Why.** Protocol 11 showed that answers timed so that their checks span another request's release move that release, and that a client decoded it. During that run the laptop, which is both the server and the observer, was also busy with other work (disclosed in `results.md`). Protocol 12 asks two questions:
1. Does the client-side result replicate with an idle laptop?
2. Does `--release-cpu 0` reduce the shift on the endpoint's clock? With it, the event loop keeps CPU 0 and every thread it starts moves to the other CPUs.

**Setup: as protocols 9-11, with these additions.**
- **Hardware and software:** the Pi 4's stock Raspberry Pi OS card, the endpoint `pge-endpoint` from `bef825f` (sha256 `fd63c803...`, `../images/flashprove-test/bin/BUILD.txt`) with `--confine seccomp-only`, the pinned `cp_verify` (`25c4a614...`) and FlashProve's stored small proof, replayed by `prover/server.py`. Direct cable, one client connection.
- **One boot.** Measuring starts at least 15 minutes after boot. The device gate (`device/flashprove_gate.sh`) runs first, on the same boot, and must pass. Then protocol 12, from the commit that adds this file or a later commit that changes no file the run uses.
- **Laptop idle.** No proving, builds or tests for any other work, no other agent sessions, both Lima VMs stopped, no repo edits. The runner records the laptop's load average and ten busiest processes before and after every arm (`OUT/NAME.laptop.txt`).
- **CPU sampling in every arm** (`CPU_SAMPLE=1`, `device/pi_cpu_sample.sh`): once a minute, the CPU list of each running `cp_verify` and each endpoint thread. It is the same in every arm, so it can't differ between the conditions.

**Arms, in this order,** through `device/flashprove_protocols.sh 12`. Each arm has 257 requests carrying 256 bits, `PERIOD_MS=7000`, deadline 10, spin 5 ms, at most 8 pending. The straddle code is `--lead 1 --shift 0.5`, as in protocol 11. U is unpinned; P is `RELEASE_CPU=0`.

| Order | Arm | Condition | Bits |
|---|---|---|---|
| 1 | `p12_SU1` | straddle, U | seed 121, bits 1-256 |
| 2 | `p12_SP1` | straddle, P | seed 122, bits 1-256 |
| 3 | `p12_HP` | honest, P | sham labels: seed 122, bits 1-256 |
| 4 | `p12_HU` | honest, U | sham labels: seed 121, bits 1-256 |
| 5 | `p12_SP2` | straddle, P | seed 122, bits 257-512 |
| 6 | `p12_SU2` | straddle, U | seed 121, bits 257-512 |

Bits use `random.Random(seed)` and one `choice('01')` per bit; a second block continues the same stream.

**Validity.**
- An arm counts only if its manifest says `"status": "ok"`, as in protocols 9-11.
- In addition, `observer/protocol12.py` refuses to give any verdict unless:
  - all six arms come from one boot (the boot id before and after each arm);
  - they come from one commit and one endpoint binary;
  - `release_cpu` is `0` in exactly the P arms;
  - CPU sampling was on.
- A failed arm is repeated in full with the same bits (`ARMS=name`; the earlier attempt is kept), never patched, and every repeat is reported.
- No block is dropped for outliers.

**Registered analyses** (all in `observer/protocol12.py`, defaults `--heldout 10000 --perms 20000 --boot 10000`):
1. **U, primary.** `observer/jitter_test.py`'s held-out decoder on `p12_SU1` + `p12_SU2` (joined, the last request of each block dropped, 512 bits), with 10000 null shuffles. Control: `p12_HU` with its sham labels (256 bits).
2. **P, primary.** The same on `p12_SP1` + `p12_SP2`, control `p12_HP`.
3. **The defence's effect, tested directly.** Release lateness on the endpoint's clock (sent minus target): the median for 1 bits minus the median for 0 bits in U and in P, and U minus P.
   - The intervals are 99 percent bootstrap intervals (10000 resamples within each block and bit).
   - **Reduced** only if the U-minus-P interval lies above zero, **increased** if below; otherwise no difference shown.
   - The honest arms' differences are reported with permutation p values.
4. **Secondaries.** They explain, never decide, and are all reported:
   - the client mean difference, as printed by `jitter_test.py`;
   - the admission path, with labels shifted by two:
     - the held-out decoder on rows 2-256 of each block;
     - the client's time minus the endpoint's admission-to-send time, **expected lower for 1 bits**;
     - the admission gap on the Pi's clock (successive admissions minus the period), **expected lower for 1 bits at a shift of two and higher at a shift of three**;
     - each for the code and the honest arms;
   - lateness against the age of the check running at each release;
   - outliers per block: client times more than one millisecond above the block median, and endpoint lateness above one millisecond.

**Decision rule, as in protocols 1-11.** For analyses 1 and 2:
- **leak shown** if the code's null p < 0.01 and its control's null p is above 0.05;
- **not detected** if the code's null p is above 0.05, reported with its sample size, never as "no leakage";
- otherwise **inconclusive**.

**Readings fixed in advance.**
- **U shown, P not detected, analysis 3 reduced:** pinning removed the detected client-side effect at 512 bits and cut the endpoint's shift by the measured amount, on this boot and device.
- **U not detected:** protocol 11's client-side result didn't replicate with an idle laptop. The endpoint's shift is still reported, and P can't be read as a defence.
- **P still shown:** sharing a CPU isn't the whole cause. Pinning doesn't isolate CPU 0 from the kernel, caches, interrupts or the governor, so this wouldn't rule out isolation as a defence.
- **Any other combination** is reported as measured, with no further claim.

**Limits fixed now.** One device, one card, one boot, one client. Each P condition's control has 256 honest bits, against 512 for the code. Pinning is not isolation: the event loop still reads and parses the answers and does the release. Protocol 12 isn't pooled with protocols 9-11, the VM runs or the pilot.

**Time.** Six arms of about half an hour each, plus the gate: about three and a half hours of Pi time. If protocol 12 hasn't run by the evening of Thu 26 Nov, it's dropped, and that's reported.

## Commands
```sh
export CONFINE=seccomp-only PY=../p01-verifier-box/.venv/bin/python
device/flashprove_gate.sh                 # same boot, must pass
CPU_SAMPLE=1 OUT=runs/p12_check device/flashprove_scenario.sh p12_check fixed 3 1 -- --mode honest
                                          # function check, not analysed: the manifest must show boot_id
                                          # before and after and cpu_sample true, and p12_check.cpu.log
                                          # endpoint lines
device/flashprove_protocols.sh 12         # OUT=runs/p12_<time>
python3 observer/protocol12.py runs/p12_<time>
```

## Notes before the first run

- None yet.
