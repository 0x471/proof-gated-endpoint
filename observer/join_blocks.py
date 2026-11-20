"""Join block traces of one arm, in run order, into one trace and one bit string for observer/jitter_test.py.

Usage: python observer/join_blocks.py OUT_TRACE OUT_BITS BLOCK.trace.jsonl:BLOCK.bits ... [--drop-last]

Each block must be a sequential or paced trace whose rows are the answers to requests 0..n-1 in order, as
jitter_test.py requires; ids are renumbered to continue across blocks. A block's bits must match its rows,
or its rows minus one with --drop-last (the straddle code: the last request of a block carries no bit,
because no later answer lands near its release). Anything that doesn't line up is refused.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def join(blocks: list[tuple[list[dict], str]], drop_last: bool = False) -> tuple[list[dict], str]:
    out, bits = [], ""
    for k, (rows, b) in enumerate(blocks):
        if not b or set(b) - {"0", "1"}:
            raise ValueError(f"block {k}: bits must be a non-empty string of 0 and 1")
        for i, r in enumerate(rows):
            if r.get("id") != i or r.get("answer", {}).get("id") != i:
                raise ValueError(f"block {k}: row {i} is not the answer to request {i}")
        keep = rows[:-1] if drop_last else rows
        if len(keep) != len(b):
            raise ValueError(f"block {k}: {len(keep)} rows for {len(b)} bits")
        for r in keep:
            n = len(out)
            out.append({**r, "id": n, "answer": {**r["answer"], "id": n}})
        bits += b
    return out, bits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_trace")
    ap.add_argument("out_bits")
    ap.add_argument("blocks", nargs="+", metavar="TRACE:BITS")
    ap.add_argument("--drop-last", action="store_true")
    a = ap.parse_args(argv)
    blocks = []
    for spec in a.blocks:
        trace, _, bits = spec.rpartition(":")
        rows = [json.loads(l) for l in Path(trace).read_text().splitlines() if l.strip()]
        blocks.append((rows, Path(bits).read_text().strip()))
    rows, bits = join(blocks, a.drop_last)
    Path(a.out_trace).write_text("".join(json.dumps(r) + "\n" for r in rows))
    Path(a.out_bits).write_text(bits + "\n")
    print(f"{len(rows)} rows from {len(blocks)} blocks")


if __name__ == "__main__":
    main()
