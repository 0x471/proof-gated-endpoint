"""Exploratory side-channel attacks on the FlashProve endpoint, on one Linux machine (a laptop VM).

Not device evidence and not a registered protocol: it shows how each attack works and what a client can
decode, to decide what the Pi measures. A compromised server hides a random message in its behaviour; the
decoder sees only what a client sees (reply status, reply order, client-side time). The endpoint's private
trace explains the mechanism. Needs Linux, PGE_RUST_BIN, PGE_CP_VERIFY and PGE_FP_FIXTURE, like
tests/test_endpoint_rust_flashprove.py, whose harness it reuses.

Usage: python observer/explore_attacks.py [--seed N] [--out FILE] [attack ...]
Attacks: timing, order, order_failure, straddle (default: all).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "observer"))
sys.path.insert(0, str(ROOT))

import test_endpoint_rust_flashprove as harness  # noqa: E402
from client import decode_order_bits, trace_batches  # noqa: E402
from pge.tokens import TokenWire, read_pins  # noqa: E402
from prover.server import Replay, Server  # noqa: E402
import relay  # noqa: E402

BIN = Path(os.environ["PGE_RUST_BIN"])
FLASHPROVE = (Path(os.environ["PGE_CP_VERIFY"]), Path(os.environ["PGE_FP_FIXTURE"]))


def run(release="fixed", **kw):
    """One endpoint run; release "arrival" swaps the harness's --release fixed (the positive control)."""
    original = harness.endpoint_args

    def args(*a, **k):
        out = original(*a, **k)
        out[out.index("--release") + 1] = release
        return out

    harness.endpoint_args = args
    try:
        return asyncio.run(harness.run_endpoint(BIN, FLASHPROVE, Path(tempfile.mkdtemp()) / "w", **kw))
    finally:
        harness.endpoint_args = original


def bits(rng, n):
    return "".join(rng.choice("01") for _ in range(n))


def threshold_decode(values, message):
    """Calibrate on the first half (midpoint of the two class means, direction from calibration), score the
    second half. Returns (accuracy on the held-out half, held-out size, mean difference 1 minus 0)."""
    half = len(values) // 2
    cal = [(v, b) for v, b in zip(values[:half], message[:half])]
    ones, zeros = [v for v, b in cal if b == "1"], [v for v, b in cal if b == "0"]
    if not ones or not zeros:
        return None, 0, None
    mid, up = (statistics.mean(ones) + statistics.mean(zeros)) / 2, statistics.mean(ones) > statistics.mean(zeros)
    test = list(zip(values[half:], message[half:]))
    right = sum((("1" if (v > mid) == up else "0") == b) for v, b in test)
    diff = statistics.mean(v for v, b in zip(values, message) if b == "1") - \
        statistics.mean(v for v, b in zip(values, message) if b == "0")
    return right / len(test), len(test), diff


def lateness_ms(records):
    return [(r["trace"]["sent"] - r["trace"]["target"]) / 1e6 for r in harness.released(records)
            if r["trace"]["target"] is not None]


def timing(rng, deadline, n=32, slot=0.5):
    """The server delays its answer by `slot` for a 1. Arrival release lets the delay through; fixed
    release should hide it, up to whatever residue the release timing keeps."""
    message, out = bits(rng, n), {}
    for release in ("arrival", "fixed"):
        rows, records, _, _ = run(release=release, mode="timing_bits", n=n, bits=message, slot=slot, deadline=deadline)
        elapsed = [r["elapsed_ms"] for r in rows]
        acc, held, diff = threshold_decode(elapsed, message)
        out[release] = {"replies_ok": sum(r["answer"]["ok"] for r in rows), "n": n, "heldout_accuracy": acc,
                        "heldout_n": held, "mean_diff_ms": diff}
        if release == "fixed":
            late = lateness_ms(records)
            out[release]["endpoint_lateness_ms"] = {"bit1": statistics.mean(l for l, b in zip(late, message) if b == "1"),
                                                    "bit0": statistics.mean(l for l, b in zip(late, message) if b == "0")}
    return {"message_bits": n, "slot_s": slot, **out}


def order(rng, deadline, batches=8):
    """The server permutes each batch of 3 answers (2 bits). Through the endpoint replies keep admission
    order; through the unchecked relay (the positive control) the permutation reaches the client."""
    message = bits(rng, 2 * batches)
    rows, _, _, _ = run(mode="order_bits", n=3 * batches, client_batch=3, bits=message, batch=3, deadline=deadline)
    endpoint_bits = decode_order_bits(rows, 3)

    async def through_relay():
        replay = Replay(FLASHPROVE[1], harness.PINS)
        server = Server(replay, mode="order_bits", bits=message, batch=3)
        up = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        wire = TokenWire.from_pins(read_pins(harness.PINS.read_text()))
        front = await relay.start(("127.0.0.1", 0), ("127.0.0.1", up.sockets[0].getsockname()[1]), wire)
        try:
            return await trace_batches("127.0.0.1", front.sockets[0].getsockname()[1], [replay.prompt] * (3 * batches),
                                       3, tokens=True)
        finally:
            front.close()
            up.close()
            server.pool.shutdown(wait=True)

    relay_bits = decode_order_bits(asyncio.run(through_relay()), 3)
    agree = lambda got: sum(a == b for a, b in zip(got, message)) / len(message)
    return {"message_bits": len(message), "endpoint_agreement": agree(endpoint_bits),
            "endpoint_decoded": endpoint_bits, "relay_agreement": agree(relay_bits), "replies_ok": sum(r["answer"]["ok"] for r in rows)}


def order_failure(rng, deadline, batches=16):
    """Two requests at once and a deadline that fits one check, not two. The single checker takes answers
    in arrival order, so the server's answer order decides which request misses its deadline: reply order
    stays correct, but the failure's position carries the bit."""
    message = bits(rng, batches)
    rows, records, _, _ = run(mode="order_bits", n=2 * batches, client_batch=2, bits=message, batch=2, deadline=deadline)
    by_id = {r["id"]: r["answer"]["ok"] for r in rows}
    decoded = ""
    for b in range(batches):
        first_ok, second_ok = by_id.get(2 * b), by_id.get(2 * b + 1)
        # Bit 0 = answers in admission order: the first is checked first and passes, the second misses.
        decoded += "0" if (first_ok and not second_ok) else "1" if (second_ok and not first_ok) else "?"
    results = [r["result"] for r in harness.released(records)]
    return {"message_bits": batches, "deadline_s": deadline, "decoded": decoded, "agreement":
            sum(a == b for a, b in zip(decoded, message)) / batches, "results": {k: results.count(k) for k in set(results)}}


def straddle(rng, deadline, n=33):
    """Answer k lands just before (1) or just after (0) request k-1's fixed release: does that release move?"""
    period, lead, shift = 2 * deadline / 3, 0.1 * deadline, 0.1 * deadline
    message = bits(rng, n - 1)
    rows, records, _, _ = run(mode="straddle_bits", n=n, client_period=period, bits=message, period=period,
                              lead=lead, shift=shift, deadline=deadline)
    elapsed = {r["id"]: r["elapsed_ms"] for r in rows}
    values = [elapsed[k] for k in range(n - 1)]  # request k-1 carries bit k-1 through answer k
    acc, held, diff = threshold_decode(values, message)
    late = lateness_ms(records)[: n - 1]
    return {"message_bits": n - 1, "replies_ok": sum(r["answer"]["ok"] for r in rows), "heldout_accuracy": acc,
            "heldout_n": held, "mean_diff_ms": diff,
            "endpoint_lateness_ms": {"bit1": statistics.mean(l for l, b in zip(late, message) if b == "1"),
                                     "bit0": statistics.mean(l for l, b in zip(late, message) if b == "0")}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("attacks", nargs="*", default=["timing", "order", "order_failure", "straddle"])
    ap.add_argument("--seed", type=int, default=20261120)
    ap.add_argument("--deadline", type=float, default=3.0, help="fixed release, seconds")
    ap.add_argument("--tight", type=float, default=1.5, help="order_failure deadline: fits one check, not two")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rng, report = random.Random(a.seed), {"seed": a.seed, "machine": os.uname().nodename, "confine": harness.CONFINE}
    for name in a.attacks:
        if name == "order_failure":
            report[name] = order_failure(rng, a.tight)
        else:
            report[name] = {"timing": timing, "order": order, "straddle": straddle}[name](rng, a.deadline if name != "order" else 2 * a.deadline)
        print(name, json.dumps(report[name]), file=sys.stderr, flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
