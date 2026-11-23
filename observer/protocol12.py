"""Protocol 12's registered analysis (docs/EXPERIMENT_PROTOCOL_6.md): the load leak with and without
--release-cpu, one boot, six blocks.

Usage: python observer/protocol12.py runs/p12_<time> [--heldout 10000] [--perms 20000] [--boot 10000]

Arms: p12_SU1 p12_SP1 p12_HP p12_HU p12_SP2 p12_SU2 (S straddle code, H honest with sham labels, U unpinned,
P pinned with RELEASE_CPU=0). Each block is 257 requests carrying 256 bits; request i carries bit i and the
last request carries none.

1. U: observer/jitter_test.py's held-out decoder on SU1+SU2 (512 bits), control HU (256 bits).
2. P: the same on SP1+SP2, control HP. Decision rule for both: leak shown if the code's null p < 0.01 and the
   control's > 0.05; not detected if the code's > 0.05; otherwise inconclusive.
3. The defence's effect: release lateness on the endpoint's clock, median for 1 bits minus 0 bits, in U and
   in P, and U minus P with a 99 % bootstrap interval, resampled within each block and bit. Reduced only if
   the interval is above zero; increased if below.
4. Secondaries, which explain and never decide: the client mean difference (printed by jitter_test.py);
   the admission path with labels shifted by two (the held-out decoder on rows 2..256 of each block; the
   client's time minus the endpoint's admission-to-send time, expected lower for 1; the admission gap on the
   Pi's clock, expected lower for 1 at shift 2 and higher at shift 3); lateness against the age of the check
   running at the release; outliers per block (client more than 1 ms above its block median, endpoint
   lateness above 1 ms).
Refuses to give verdicts unless all six manifests are ok, from one boot, one commit and one binary, with
RELEASE_CPU 0 in exactly the P arms and CPU sampling on.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import random
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jitter_test  # noqa: E402
from join_blocks import join  # noqa: E402
from trace_lateness import lateness, median_diff, permutation_p  # noqa: E402

ARMS = ["p12_SU1", "p12_SP1", "p12_HP", "p12_HU", "p12_SP2", "p12_SU2"]
PINNED = {"p12_SP1", "p12_HP", "p12_SP2"}
PERIOD_MS = 7000.0


def load(out: Path, arm: str) -> dict:
    rows = [json.loads(l) for l in (out / f"{arm}.trace.jsonl").read_text().splitlines() if l.strip()]
    records = [json.loads(l) for l in (out / f"{arm}.endpoint.jsonl").read_text().splitlines() if l.strip()]
    bits = (out / f"{arm}.bits").read_text().strip()
    manifest = json.loads((out / f"{arm}.manifest.json").read_text())
    traces = {r["trace"]["cid"]: r["trace"] for r in records if "trace" in r and "cid" in r["trace"]}
    if len(rows) != len(bits) + 1 or any(r.get("id") != i for i, r in enumerate(rows)):
        raise ValueError(f"{arm}: expected rows 0..{len(bits)} for {len(bits)} bits")
    if sorted(traces) != list(range(len(rows))):
        raise ValueError(f"{arm}: endpoint traces don't cover requests 0..{len(rows) - 1}")
    return {"rows": rows, "records": records, "bits": bits, "manifest": manifest, "traces": traces}


def validity(arms: dict[str, dict]) -> list[str]:
    problems = []
    first = arms[ARMS[0]]["manifest"]
    for name, a in arms.items():
        m = a["manifest"]
        if m.get("status") != "ok":
            problems.append(f"{name}: status {m.get('status')}")
        if m.get("release_cpu") != ("0" if name in PINNED else ""):
            problems.append(f"{name}: release_cpu {m.get('release_cpu')!r}")
        if not m.get("cpu_sample"):
            problems.append(f"{name}: CPU sampling was off")
        for key in ("commit", "endpoint_sha256"):
            if m.get(key) != first.get(key):
                problems.append(f"{name}: {key} differs from {ARMS[0]}")
        before = [l.split()[1] for l in m.get("pi_before", []) if l.startswith("boot_id ") and len(l.split()) == 2]
        after = [l.split()[1] for l in m.get("pi_after", []) if l.startswith("boot_id ") and len(l.split()) == 2]
        if len(before) != 1 or before != after:
            problems.append(f"{name}: boot id missing before or after the arm, or changed during it")
        a["boot"] = before[0] if len(before) == 1 and before == after else None
    if len({a.get("boot") for a in arms.values()}) != 1:
        problems.append("the arms are not from one boot")
    return problems


def heldout(trace: Path, bits: str, shuffles: int, perms: int) -> tuple[str, float]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        jitter_test.main([str(trace), bits, "--perms", str(perms), "--heldout", str(shuffles)])
    text = buf.getvalue()
    m = re.search(r"held-out errors .*null P\(better half <= [0-9.]+\) = ([0-9.]+)", text)
    if not m:
        raise RuntimeError(f"no held-out line in jitter_test output:\n{text}")
    return text.rstrip(), float(m.group(1))


def verdict(code_p: float, control_p: float) -> str:
    if code_p < 0.01 and control_p > 0.05:
        return "leak shown"
    if code_p > 0.05:
        return "not detected"
    return "inconclusive"


def write_joined(out: Path, label: str, blocks: list[tuple[list[dict], str]], drop_last: bool) -> tuple[Path, str]:
    rows, bits = join(blocks, drop_last)
    trace = out / f"{label}.jsonl"
    trace.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / f"{label}.bits").write_text(bits + "\n")
    return trace, bits


def shifted_rows(a: dict, shift: int) -> tuple[list[dict], str]:
    """Rows shift..last of a block, row j labelled with bit j - shift, renumbered from 0."""
    rows, bits = a["rows"], a["bits"]
    keep = [(r, bits[j - shift]) for j, r in enumerate(rows) if 0 <= j - shift < len(bits)]
    out = [{**r, "id": n, "answer": {**r["answer"], "id": n}} for n, (r, _) in enumerate(keep)]
    return out, "".join(b for _, b in keep)


def lateness_pairs(a: dict) -> list[tuple[float, str]]:
    return lateness(a["records"], a["bits"])


def boot_diff(blocks_u: list[list[tuple[float, str]]], blocks_p: list[list[tuple[float, str]]], reps: int,
              seed: int = 12) -> dict:
    """Bootstrap within block and bit: d(U), d(P) and d(U) - d(P), with 99 % percentile intervals."""
    def resample(blocks, rng):
        out = []
        for pairs in blocks:
            for b in "01":
                xs = [x for x, bit in pairs if bit == b]
                out += [(rng.choice(xs), b) for _ in xs]
        return out

    rng = random.Random(seed)
    du, dp, dd = [], [], []
    for _ in range(reps):
        u, p = median_diff(resample(blocks_u, rng)), median_diff(resample(blocks_p, rng))
        du.append(u)
        dp.append(p)
        dd.append(u - p)

    def ci(xs):
        xs = sorted(xs)
        return xs[int(0.005 * (len(xs) - 1))], xs[int(round(0.995 * (len(xs) - 1)))]

    pool_u = [x for b in blocks_u for x in b]
    pool_p = [x for b in blocks_p for x in b]
    return {"d_u": median_diff(pool_u), "ci_u": ci(du), "d_p": median_diff(pool_p), "ci_p": ci(dp),
            "diff": median_diff(pool_u) - median_diff(pool_p), "ci_diff": ci(dd)}


def effect_reading(ci: tuple[float, float]) -> str:
    if ci[0] > 0:
        return "reduced"
    if ci[1] < 0:
        return "increased"
    return "no difference shown"


def admission_metrics(a: dict, shift: int) -> list[tuple[float, float | None, str]]:
    """(client time minus admission-to-send, admission gap minus the period, label) per row with a label."""
    out = []
    rows, tr, bits = a["rows"], a["traces"], a["bits"]
    for j, r in enumerate(rows):
        i = j - shift
        if not 0 <= i < len(bits):
            continue
        m = r["elapsed_ms"] - (tr[j]["sent"] - tr[j]["admit"]) / 1e6
        g = (tr[j]["admit"] - tr[j - 1]["admit"]) / 1e6 - PERIOD_MS if j > 0 else None
        out.append((m, g, bits[i]))
    return out


def median_perm(values: list[tuple[float, str]], perms: int, seed: int = 0) -> tuple[float, float]:
    return median_diff(values), permutation_p(values, perms, random.Random(seed))


def running_check_age(a: dict) -> list[tuple[str, float | None, float]]:
    """(bit, age in s of another request's check running at this release or None, lateness ms)."""
    tr, out = a["traces"], []
    for i, b in enumerate(a["bits"]):
        t = tr[i]
        ages = [(t["target"] - o["check_start"]) / 1e9 for k, o in tr.items()
                if k != i and o.get("check_start") is not None and o.get("check_end") is not None
                and o["check_start"] <= t["target"] <= o["check_end"]]
        out.append((b, min(ages) if ages else None, (t["sent"] - t["target"]) / 1e6))
    return out


def outliers(a: dict) -> tuple[int, float, int, float]:
    client = [r["elapsed_ms"] for r in a["rows"][:-1]]
    med = statistics.median(client)
    late = [x for x, _ in lateness_pairs(a)]
    return (sum(c > med + 1 for c in client), max(client) - med, sum(x > 1 for x in late), max(late))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--heldout", type=int, default=10000)
    ap.add_argument("--perms", type=int, default=20000)
    ap.add_argument("--boot", type=int, default=10000)
    a = ap.parse_args(argv)
    out = Path(a.out)
    arms = {name: load(out, name) for name in ARMS}
    problems = validity(arms)
    print("validity:", "ok" if not problems else "; ".join(problems))
    if problems:
        return 2

    print("\n1-2. Held-out decoder (registered primary)")
    verdicts = {}
    for cond, s1, s2, h in (("U", "p12_SU1", "p12_SU2", "p12_HU"), ("P", "p12_SP1", "p12_SP2", "p12_HP")):
        trace, bits = write_joined(out, f"S{cond}", [(arms[s1]["rows"], arms[s1]["bits"]),
                                                      (arms[s2]["rows"], arms[s2]["bits"])], True)
        text, code_p = heldout(trace, bits, a.heldout, a.perms)
        ctrace, cbits = write_joined(out, f"H{cond}", [(arms[h]["rows"], arms[h]["bits"])], True)
        ctext, control_p = heldout(ctrace, cbits, a.heldout, a.perms)
        verdicts[cond] = verdict(code_p, control_p)
        print(f"{cond} code ({s1}+{s2}):\n{text}\n{cond} control ({h}):\n{ctext}\n{cond}: {verdicts[cond]}")

    print("\n3. Release lateness by bit, endpoint's clock (ms)")
    b = boot_diff([lateness_pairs(arms["p12_SU1"]), lateness_pairs(arms["p12_SU2"])],
                  [lateness_pairs(arms["p12_SP1"]), lateness_pairs(arms["p12_SP2"])], a.boot)
    print(f"U: {b['d_u']:+.4f} (99 % interval {b['ci_u'][0]:+.4f} to {b['ci_u'][1]:+.4f})")
    print(f"P: {b['d_p']:+.4f} (99 % interval {b['ci_p'][0]:+.4f} to {b['ci_p'][1]:+.4f})")
    print(f"U minus P: {b['diff']:+.4f} (99 % interval {b['ci_diff'][0]:+.4f} to {b['ci_diff'][1]:+.4f}): "
          f"{effect_reading(b['ci_diff'])}  ({a.boot} resamples within block and bit)")
    for name in ("p12_HU", "p12_HP"):
        d, p = median_perm(lateness_pairs(arms[name]), a.perms)
        print(f"{name} (honest, sham labels): {d:+.4f}, permutation p {p:.4f}")

    print("\n4. Secondaries (explain, never decide)")
    for cond, s1, s2, h in (("U", "p12_SU1", "p12_SU2", "p12_HU"), ("P", "p12_SP1", "p12_SP2", "p12_HP")):
        blocks = [shifted_rows(arms[s1], 2), shifted_rows(arms[s2], 2)]
        trace, bits = write_joined(out, f"S{cond}_shift2", blocks, False)
        text, _ = heldout(trace, bits, a.heldout, a.perms)
        print(f"{cond} decoder with labels shifted by 2 (rows 2..256 of each block):\n{text}")
        hblock = shifted_rows(arms[h], 2)
        htrace, hbits = write_joined(out, f"H{cond}_shift2", [hblock], False)
        htext, _ = heldout(htrace, hbits, a.heldout, a.perms)
        print(f"{cond} honest, shifted by 2:\n{htext}")
        for label, names in ((f"{cond} code", (s1, s2)), (f"{cond} honest", (h,))):
            for shift in (2, 3):
                ms = [x for n in names for x in admission_metrics(arms[n], shift)]
                if shift == 2:
                    d, p = median_perm([(m, l) for m, _, l in ms], a.perms)
                    print(f"{label}: client minus admission-to-send, shift 2: {d:+.4f} ms, p {p:.4f} "
                          f"(expected below 0)")
                d, p = median_perm([(g, l) for _, g, l in ms if g is not None], a.perms)
                print(f"{label}: admission gap on the Pi's clock, shift {shift}: {d:+.4f} ms, p {p:.4f} "
                      f"(expected {'below' if shift == 2 else 'above'} 0)")
    for name in ARMS:
        ages = running_check_age(arms[name])
        for bit in "01":
            sel = [(age, late) for b_, age, late in ages if b_ == bit]
            busy = [(age, late) for age, late in sel if age is not None]
            idle = [late for age, late in sel if age is None]
            busy_txt = (f"check running at {len(busy)}: median age {statistics.median(x for x, _ in busy):.2f} s, "
                        f"lateness {statistics.median(y for _, y in busy):.4f} ms" if busy else "no check running")
            idle_txt = f"; idle at {len(idle)}: lateness {statistics.median(idle):.4f} ms" if idle else ""
            print(f"{name} bit {bit}: {busy_txt}{idle_txt}")
    for name in ARMS:
        n_client, max_client, n_late, max_late = outliers(arms[name])
        print(f"{name}: client outliers {n_client} (max {max_client:.2f} ms above median); "
              f"endpoint lateness above 1 ms {n_late} (max {max_late:.2f} ms)")
    print(f"\nRegistered readings: U {verdicts['U']}; P {verdicts['P']}; effect {effect_reading(b['ci_diff'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
