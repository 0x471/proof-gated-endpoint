# Experiment protocols 9-11: timing, order and load with FlashProve on the Pi (written 20 Nov, before any run)

Registered after the pilot of 20 Nov (`device/flashprove_pilot.sh`; its numbers are in `book/src/results.md`, "FlashProve on the Pi") and before any confirmatory data. The pilot chose the deadline, the slot, the pacing and the sample sizes below; no pilot data is pooled with these runs.

**Common setup.**
- The Pi 4's stock Raspberry Pi OS card, the endpoint `pge-endpoint` built from `bef825f` (sha256 `fd63c803...`, `../images/flashprove-test/bin/BUILD.txt`) with `--confine seccomp-only`, the pinned `cp_verify` (`25c4a614...`) and FlashProve's stored small proof, replayed by `prover/server.py`.
- Direct cable, the laptop as server and client, one client connection. Fixed release with a 5 ms spin unless an arm says otherwise.
- Measuring starts at least 15 minutes after boot. Laptop idle, both Lima VMs stopped, no repo edits during a protocol.
- Every arm runs through `device/flashprove_protocols.sh`, which fixes the order, writes each arm's bits from its registered seed before the arm starts, and records a manifest per arm (commit, hashes of what the Pi ran, CPU settings, temperature, throttling).
- Bits: `random.Random(seed)`, one `choice('01')` per bit; a second block continues the same stream. Honest arms are analysed with their paired code arm's bits as sham labels.
- **Validity.** An arm counts only if its manifest says `"status": "ok"`: every reply arrived, both endpoint logs were copied, no throttling. A failed arm is repeated in full with the same bits (`ARMS=name`; the earlier attempt is kept in `attempt_*`), never patched. Every repeat is reported.
- **Decision rule**, as in protocols 1-8: a leak is shown if the code arm's primary p < 0.01 (and, where there is one, its control's p > 0.05); "not detected" if the code arm's p > 0.05, reported with its sample size and the pilot's simulated sensitivity, never as "no leakage"; anything else is inconclusive.
- **Limits fixed now.** One device, one card, one boot per protocol, one client. These results are not pooled with the EZKL runs, the VM runs or the pilot.

## Protocol 9: does reply time carry the timing code through fixed release?

**Question.** With deadline 10, does the client's reply time carry the `timing_bits` code (1 s slot) under fixed release?

**Arms, in this order:**
1. `p9_arrival`: arrival release, 64 replies, seed 92. Positive control.
2. `p9_T1`, `p9_H1`, `p9_H2`, `p9_T2`, ABBA, 256 replies each. T is the code; H is honest traffic.
3. T and H share the seed-91 stream: bits 1-256 for the first blocks, 257-512 for the second.

**Primary analysis.**
- Join `p9_T1`, `p9_T2` and `p9_H1`, `p9_H2` with `observer/join_blocks.py`.
- Run `observer/jitter_test.py JOINED_T BITS --control JOINED_H --heldout 2000 --perms 20000`. The primary p is the held-out decoder's null probability.
- The positive control must be decoded (held-out errors at most 0.1 in both halves), or protocol 9 is invalid and is reported as such.

**Secondary** (reported, never used for the decision): the mean difference and its permutation p (same command); release lateness by bit from the endpoint's private trace. The trace explains a mechanism; it never decides.

**Reading fixed now.** Not detected means no client-visible residue under this decoder at n = 512. The pilot's simulated sensitivity for that decoder and size (`book/src/results.md`, "FlashProve on the Pi") says how large an additive shift it would usually catch, conditional on the pilot's honest replies. It is not a bound on smaller shifts or cleverer decoders.

## Protocol 10: reply order, and order turned into failures

**Arms, in this order:**

| Arm | Setting | Requests | Seed |
|---|---|---|---|
| `p10_order` | `order_bits`, batches of 3, deadline 20 | 192 (128 bits) | 101 |
| `p10_orderfail` | `order_bits`, batches of 2, deadline 7.5 | 256 (128 bits) | 102 |
| `p10_orderfail_wide` | the same code, deadline 12. Negative control: every check fits | 256 | 103 |
| `p10_orderfail_cap` | the order-to-failure code with the failure cap `--cap-window 8 --cap-failures 2 --cap-cooldown 8` | 256 | 104 |
| `p10_pending1` | honest traffic, batches of 2, `--max-pending 1`, deadline 10. Measures the defence's cost | 256 | 105 (unused) |

**Primary analysis.** `observer/protocol10.py OUT`:
- **Order:** batches that left out of admission order. The invariant holds if there are none; any is a finding, whatever else happens. Also the order code's decoded bits against Binomial(128, ½).
- **Order to failure:** bits decoded from which request of each pair failed, against Binomial(128, ½), with "?" counted as a miss, under the decision rule.
- **Wide control:** its failures and decoded bits.
- **Cap:** bits decoded, the count of `capped` replies and the ok replies. Compared with the uncapped arm descriptively, not tested.
- **Max-pending 1:** ok and `unavailable` replies only. With one request pending there is no queue for the server to order, so the leak is removed by construction; the arm measures what that costs honest traffic.

**Positive control for order.** `observer/relay.py` passes answers through in the server's order, unchecked. It is re-run in the VM with its own random bits (`observer/explore_attacks.py order`) before this protocol is analysed. It shows that the encoder and decoder work; it needs no Pi.

**Prediction recorded now** (from the pilot, not part of the rule): `p10_orderfail` shows a leak; the wide control shows none.

## Protocol 11: does a check running near a release move it?

**Question.** One request every 7 seconds (`PERIOD_MS=7000`) with deadline 10, and the `straddle_bits` code: answer k arrives a second before (bit 1) or half a second after (bit 0) request k−1's release (`--lead 1 --shift 0.5`). Does the client's reply time for request k−1 follow the bit?

**Arms, in this order:** `p11_S1`, `p11_H1`, `p11_H2`, `p11_S2`, ABBA, 257 requests each (256 bits), seed 111 as in protocol 9.

**Primary analysis.** Join with `observer/join_blocks.py --drop-last` (the last request of a block carries no bit), then `observer/jitter_test.py` on the joined S trace with the joined H trace as control, `--heldout 2000 --perms 20000`, under the decision rule.

**Secondary** (explains, never decides): release lateness by bit on the endpoint's clock, median difference and a 20000-shuffle permutation p. The pilot saw a shift there that the client didn't read at 32 bits. If the client stays at "not detected" while the endpoint shift is clear, both are reported: the delay exists, and this client didn't see it at this sample size.

## Order of protocols and time

Protocols 10, 9, 11, each on its own boot if possible. Rough Pi time, from the pilot's cycle times: about 1.7 hours for 10, 3 hours for 9 and 2 hours for 11. If time runs short, protocol 10 and the protocol 9 code/honest pair come first; nothing is dropped because of favourable or unfavourable p values. A reboot repeat of the key arm (`p10_orderfail`) is optional and reported separately.

## Commands
```sh
export CONFINE=seccomp-only PY=../p01-verifier-box/.venv/bin/python
device/flashprove_protocols.sh 10      # OUT=runs/p10_<time>
python3 observer/protocol10.py runs/p10_<time>
device/flashprove_protocols.sh 9
O=runs/p9_<time>
python3 observer/join_blocks.py $O/T.jsonl $O/T.bits $O/p9_T1.trace.jsonl:$O/p9_T1.bits $O/p9_T2.trace.jsonl:$O/p9_T2.bits
python3 observer/join_blocks.py $O/H.jsonl $O/H.bits $O/p9_H1.trace.jsonl:$O/p9_H1.bits $O/p9_H2.trace.jsonl:$O/p9_H2.bits
python3 observer/jitter_test.py $O/T.jsonl $(cat $O/T.bits) --control $O/H.jsonl --heldout 2000 --perms 20000
python3 observer/jitter_test.py $O/p9_arrival.trace.jsonl $(cat $O/p9_arrival.bits) --heldout 2000   # positive control
device/flashprove_protocols.sh 11
O=runs/p11_<time>   # the same two joins with --drop-last, on p11_S1/S2 and p11_H1/H2, then jitter_test.py
```

## Notes before the first run
