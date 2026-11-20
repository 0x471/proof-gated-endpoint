"""Summarise a FlashProve pilot directory (device/flashprove_pilot.sh): exploratory, not a registered analysis.

Per arm: run status, temperature range, the endpoint's check time, admission-to-verdict time, answer
arrival and release lateness (from its private trace), the client's reply times, and what a client-side
decoder recovers: the timing code from reply times (threshold set on the first half, scored on the second),
reply order, which request of a pair failed, and the straddle code. The endpoint trace explains; it never
feeds a decoder. Then a simulated sensitivity for the fixed-release timing test, conditional on the honest
arm: honest reply times resampled in blocks, an added shift for each 1 bit, the same decoder, and a one-sided
binomial test on the held-out half at alpha 0.01.

Usage: python observer/pilot_summary.py OUT [--sims 400]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import decode_order_bits  # noqa: E402

ARMS = ["pilot_honest", "pilot_timing", "pilot_timing_arrival", "pilot_order", "pilot_orderfail",
        "pilot_orderfail_wide", "pilot_straddle"]


def load(out: Path, name: str) -> dict | None:
    if not (out / f"{name}.trace.jsonl").exists():
        return None
    read = lambda s: [json.loads(l) for l in (out / f"{name}.{s}").read_text().splitlines() if l.strip()]
    temps = [int(l.split()[1]) / 1000 for l in (out / f"{name}.temp.log").read_text().splitlines()
             if len(l.split()) == 2] if (out / f"{name}.temp.log").exists() else []
    manifest = json.loads((out / f"{name}.manifest.json").read_text())
    bits = (out / f"{name}.bits").read_text().strip() if (out / f"{name}.bits").exists() else ""
    return {"rows": read("trace.jsonl"), "records": [r for r in read("endpoint.jsonl") if "trace" in r],
            "temps": temps, "manifest": manifest, "bits": bits}


def spread(xs: list[float]) -> dict | None:
    if not xs:
        return None
    s = sorted(xs)
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]
    return {"n": len(s), "min": round(s[0], 3), "median": round(statistics.median(s), 3), "p95": round(q(0.95), 3),
            "max": round(s[-1], 3)}


def ms(a, b):
    return None if a is None or b is None else (b - a) / 1e6


def binom_tail(k: int, n: int) -> float:
    """P(X >= k) for X ~ Binomial(n, 1/2)."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def threshold_decode(values: list[float], bits: str) -> dict | None:
    half = len(values) // 2
    cal = list(zip(values[:half], bits[:half]))
    ones, zeros = [v for v, b in cal if b == "1"], [v for v, b in cal if b == "0"]
    if not ones or not zeros:
        return None
    mid, up = (statistics.mean(ones) + statistics.mean(zeros)) / 2, statistics.mean(ones) > statistics.mean(zeros)
    test = list(zip(values[half:], bits[half:]))
    right = sum((("1" if (v > mid) == up else "0") == b) for v, b in test)
    all1 = [v for v, b in zip(values, bits) if b == "1"]
    all0 = [v for v, b in zip(values, bits) if b == "0"]
    return {"heldout_right": right, "heldout_n": len(test), "p_one_sided": round(binom_tail(right, len(test)), 5),
            "mean_diff_ms": round(statistics.mean(all1) - statistics.mean(all0), 3) if all1 and all0 else None}


def timing_view(arm: dict, values: list[float]) -> dict:
    late = {r["seq"]: ms(r["trace"]["target"], r["trace"]["sent"]) for r in arm["records"]}
    by_bit = {}
    seqs = sorted(late)
    for b in "01":
        xs = [late[s] for s, bit in zip(seqs, arm["bits"]) if bit == b and late[s] is not None]
        by_bit[b] = spread(xs)
    return {"decode": threshold_decode(values, arm["bits"][:len(values)]), "endpoint_lateness_by_bit_ms": by_bit}


def summarise(name: str, arm: dict) -> dict:
    rows, recs, m = arm["rows"], arm["records"], arm["manifest"]
    t = [r["trace"] for r in recs]
    results = {}
    for r in recs:
        results[r["result"]] = results.get(r["result"], 0) + 1
    out = {"status": m["status"], "release": m["release"], "deadline_s": m["deadline"], "n": m["n"],
           "batch": m["batch"], "period_ms": m["period_ms"],
           "throttled_after": [l for l in m["pi_after"] if l.startswith("throttled")],
           "temp_c": spread(arm["temps"]), "replies_ok": sum(r["answer"]["ok"] for r in rows),
           "endpoint_results": results,
           "check_ms": spread([ms(x["check_start"], x["check_end"]) for x, r in zip(t, recs)
                               if r["result"] == "accept" and x["check_end"] is not None]),
           "admit_to_verdict_ms": spread([ms(x["admit"], x["check_end"]) for x, r in zip(t, recs)
                                         if r["result"] == "accept" and x["check_end"] is not None]),
           "answer_after_admit_ms": spread([ms(x["admit"], x["answer"]) for x in t if x["answer"] is not None]),
           "release_lateness_ms": spread([ms(x["target"], x["sent"]) for x in t if x["target"] is not None]),
           "client_reply_ms": spread([r["elapsed_ms"] for r in rows])}
    admits = sorted(x["admit"] for x in t)
    if m["batch"] == 1 and not m["period_ms"] and len(admits) > 1:
        out["cycle_ms"] = spread([(b - a) / 1e6 for a, b in zip(admits, admits[1:])])
    bits = arm["bits"]
    if name in ("pilot_honest", "pilot_timing", "pilot_timing_arrival"):
        by_id = {r["id"]: r["elapsed_ms"] for r in rows}
        out.update(timing_view(arm, [by_id[i] for i in sorted(by_id)]))
    elif name == "pilot_order":
        got = decode_order_bits(rows, 3)
        batches = [[r["id"] for r in rows[i:i + 3]] for i in range(0, len(rows), 3)]
        out["order"] = {"decoded": got, "agreement": round(sum(a == b for a, b in zip(got, bits)) / len(bits), 4),
                        "batches_out_of_admission_order": sum(ids != sorted(ids) for ids in batches)}
    elif name.startswith("pilot_orderfail"):
        ok = {r["id"]: r["answer"]["ok"] for r in rows}
        dec = "".join("0" if ok.get(2 * k) and not ok.get(2 * k + 1) else
                      "1" if ok.get(2 * k + 1) and not ok.get(2 * k) else "?" for k in range(len(bits)))
        out["order_failure"] = {"decoded": dec, "right": sum(a == b for a, b in zip(dec, bits)), "bits": len(bits)}
    elif name == "pilot_straddle":
        by_id = {r["id"]: r["elapsed_ms"] for r in rows}
        n = m["n"]
        values = [by_id[k] for k in range(n - 1)]
        late = {r["seq"]: ms(r["trace"]["target"], r["trace"]["sent"]) for r in recs}
        seqs = sorted(late)[: n - 1]
        out["decode"] = threshold_decode(values, bits[: n - 1])
        out["endpoint_lateness_by_bit_ms"] = {b: spread([late[s] for s, bit in zip(seqs, bits) if bit == b])
                                              for b in "01"}
    return out


def power(residuals: list[float], n: int, shift: float, sims: int, rng: random.Random, block: int = 8,
          alpha: float = 0.01) -> float:
    """Share of simulated n-reply runs whose held-out decoder beats chance at alpha (one-sided binomial)."""
    hits = 0
    for _ in range(sims):
        series = []
        while len(series) < n:
            start = rng.randrange(len(residuals))
            series += [residuals[(start + j) % len(residuals)] for j in range(block)]
        bits = "".join(rng.choice("01") for _ in range(n))
        values = [v + (shift if b == "1" else 0.0) for v, b in zip(series[:n], bits)]
        d = threshold_decode(values, bits)
        hits += d is not None and d["p_one_sided"] < alpha
    return hits / sims


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--sims", type=int, default=400)
    a = ap.parse_args(argv)
    out = Path(a.out)
    report = {}
    for name in ARMS:
        arm = load(out, name)
        if arm is not None:
            report[name] = summarise(name, arm)
    honest = load(out, "pilot_honest")
    if honest is not None:
        values = [r["elapsed_ms"] for r in sorted(honest["rows"], key=lambda r: r["id"])]
        centre = statistics.median(values)
        residuals = [v - centre for v in values]
        rng = random.Random(20261120)
        grid = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
        report["simulated_sensitivity"] = {
            "note": "conditional on the pilot's honest reply times, resampled in blocks of 8; not a measurement",
            "alpha": 0.01, "sims": a.sims,
            "power": {str(n): {str(s): power(residuals, n, s, a.sims, rng) for s in grid} for n in (256, 512)}}
    (out / "pilot_summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
