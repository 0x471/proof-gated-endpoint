"""Registered analysis for protocol 10 (docs/EXPERIMENT_PROTOCOL_6.md): reply order and order to failure.

Usage: python observer/protocol10.py OUT
Reads OUT/NAME.trace.jsonl, OUT/NAME.bits and OUT/NAME.endpoint.jsonl for the arms below and prints one
JSON object. Every decoder uses only what the client saw. Under no leak each decoded bit matches a fair,
independent random bit with probability 1/2, so the count of matching bits is Binomial(n, 1/2); for the
registered tests a "?" (no single failure in a pair, or a batch out of shape) counts as a miss. Only the
registered arms get a verdict (the order code and p10_orderfail); the wide control and the cap arm are
descriptive, so they report decoded, correct and erased bits, the "?" pairs the receiver can see were lost.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import decode_order_bits  # noqa: E402
from pilot_summary import binom_tail  # noqa: E402


def verdict(p: float) -> str:
    return "leak shown" if p < 0.01 else "not detected" if p > 0.05 else "inconclusive"


def order_result(rows: list[dict], bits: str, batch: int = 3) -> dict:
    """Reply order on one connection: batches whose replies didn't leave in admission order, and the order code."""
    batches = [[r["id"] for r in rows[i:i + batch]] for i in range(0, len(rows), batch)]
    got = decode_order_bits(rows, batch)
    right = sum(a == b for a, b in zip(got, bits))
    p = binom_tail(right, len(bits))
    return {"batches": len(batches), "out_of_admission_order": sum(ids != sorted(ids) for ids in batches),
            "decoded_right": right, "bits": len(bits), "p": p, "verdict": verdict(p)}


def orderfail_result(rows: list[dict], bits: str, registered: bool = True) -> dict:
    """Pairs of requests: bit 0 when the first of the pair alone is ok, 1 when the second alone is."""
    ok = {r["id"]: r["answer"]["ok"] for r in rows}
    dec = "".join("0" if ok.get(2 * k) and not ok.get(2 * k + 1) else
                  "1" if ok.get(2 * k + 1) and not ok.get(2 * k) else "?" for k in range(len(bits)))
    right = sum(a == b for a, b in zip(dec, bits))
    out = {"replies_ok": sum(ok.values()), "replies": len(rows), "bits": len(bits),
           "decoded": len(bits) - dec.count("?"), "decoded_right": right, "erased": dec.count("?")}
    if registered:
        out["p"] = binom_tail(right, len(bits))
        out["verdict"] = verdict(out["p"])
    return out


def endpoint_results(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if "result" in r and r["result"] != "start":
            out[r["result"]] = out.get(r["result"], 0) + 1
    return out


def main(argv=None):
    out = Path((argv or sys.argv[1:])[0])
    rows = lambda n: [json.loads(l) for l in (out / f"{n}.trace.jsonl").read_text().splitlines() if l.strip()]
    bits = lambda n: (out / f"{n}.bits").read_text().strip()
    report = {"p10_order": order_result(rows("p10_order"), bits("p10_order"))}
    for n in ("p10_orderfail", "p10_orderfail_wide", "p10_orderfail_cap"):
        report[n] = orderfail_result(rows(n), bits(n), registered=n == "p10_orderfail")
    for n in ("p10_orderfail", "p10_orderfail_wide", "p10_orderfail_cap", "p10_pending1"):
        report.setdefault(n, {})["endpoint_results"] = endpoint_results(out / f"{n}.endpoint.jsonl")
    p1 = rows("p10_pending1")
    report["p10_pending1"].update({"replies_ok": sum(r["answer"]["ok"] for r in p1), "replies": len(p1)})
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
