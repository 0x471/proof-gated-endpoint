"""Release lateness by bit, from the endpoint's private trace: the registered secondary analysis of protocols 9
and 11 (docs/EXPERIMENT_PROTOCOL_6.md). It explains a mechanism; it never decides.

Usage: python observer/trace_lateness.py BLOCK.endpoint.jsonl:BLOCK.bits ... [--perms 20000] [--seed 0]

Each block is one endpoint run. Its released records, in sequence order, are its requests in admission order;
request i carries bit i, and requests past the end of the bits (the straddle code's last request) are left
out. Lateness is sent minus target, in ms. Reports the median for 1 bits minus the median for 0 bits and a
two-sided permutation p: the bits are shuffled across all requests, the same statistic recomputed.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path


def lateness(records: list[dict], bits: str) -> list[tuple[float, str]]:
    if not bits or set(bits) - {"0", "1"}:
        raise ValueError("bits must be a non-empty string of 0 and 1")
    released = sorted((r for r in records if "trace" in r and r["trace"].get("target") is not None),
                      key=lambda r: r["seq"])
    for i, r in enumerate(released, 1):
        if r["seq"] != i:
            raise ValueError(f"expected released sequence {i}, got {r['seq']}")
        if "cid" in r["trace"] and r["trace"]["cid"] != i - 1:
            raise ValueError(f"sequence {i} is not request {i - 1}")
    if len(released) < len(bits):
        raise ValueError(f"{len(released)} released records for {len(bits)} bits")
    return [((r["trace"]["sent"] - r["trace"]["target"]) / 1e6, b) for r, b in zip(released, bits)]


def median_diff(pairs: list[tuple[float, str]]) -> float:
    one = [x for x, b in pairs if b == "1"]
    zero = [x for x, b in pairs if b == "0"]
    return statistics.median(one) - statistics.median(zero)


def permutation_p(pairs: list[tuple[float, str]], perms: int, rng: random.Random) -> float:
    observed = abs(median_diff(pairs))
    values, labels = [x for x, _ in pairs], [b for _, b in pairs]
    hits = 0
    for _ in range(perms):
        rng.shuffle(labels)
        hits += abs(median_diff(list(zip(values, labels)))) >= observed
    return (hits + 1) / (perms + 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("blocks", nargs="+", metavar="ENDPOINT_JSONL:BITS")
    ap.add_argument("--perms", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    pairs = []
    for spec in a.blocks:
        log, _, bits = spec.rpartition(":")
        records = [json.loads(l) for l in Path(log).read_text().splitlines() if l.strip()]
        pairs += lateness(records, Path(bits).read_text().strip())
    by = {b: [x for x, bb in pairs if bb == b] for b in "01"}
    print(json.dumps({"n": len(pairs), "n1": len(by["1"]), "n0": len(by["0"]),
                      "median_1_ms": round(statistics.median(by["1"]), 4),
                      "median_0_ms": round(statistics.median(by["0"]), 4),
                      "median_diff_ms": round(median_diff(pairs), 4),
                      "permutation_p": permutation_p(pairs, a.perms, random.Random(a.seed))}))


if __name__ == "__main__":
    main()
