"""Data figures for the book and the report, drawn as plain SVG from saved runs. Nothing here decides anything:
the registered analyses do (observer/protocol10.py, jitter_test.py, trace_lateness.py); these only show the data.

Usage: python observer/figures.py --p9 runs/p9_<time> --p10 runs/p10_<time> --p11 runs/p11_<time> --out DIR

Writes order-to-failure.svg (real request pairs from protocol 10), protocol10-arms.svg (every reply of each
arm), timing-p9.svg (reply times by bit with arrival and fixed release) and load-p11.svg (release lateness by
bit). Every value comes from the run files; points beyond an axis are drawn at its edge and counted.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trace_lateness import lateness  # noqa: E402

INK, MUTED, GRID, ONE, ZERO, OK, BAD = "#1b1b1b", "#6b6b6b", "#dcdcdc", "#b3122e", "#3f4a5a", "#2f6f4f", "#b3122e"
FONT = "font-family=\"Helvetica, Arial, sans-serif\""


def text(x, y, s, size=12, anchor="start", color=INK, weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{color}" '
            f'font-weight="{weight}" {FONT}>{escape(s)}</text>')


def svg(width, height, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {width} {height}" '
            f'style="max-width: {width}px; background-color: white;" role="img">'
            f'<rect width="{width}" height="{height}" fill="white"/>{"".join(body)}</svg>\n')


def jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def ticks(lo, hi, n=5):
    span = hi - lo
    raw = span / n
    mag = 10 ** __import__("math").floor(__import__("math").log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = __import__("math").ceil(lo / step) * step
    out, v = [], start
    while v <= hi + 1e-12:
        out.append(round(v, 10))
        v += step
    return out


def fmt(v):
    return f"{v:g}"


def axis(x0, x1, y, lo, hi, label):
    body = [f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="{MUTED}"/>']
    for t in ticks(lo, hi):
        x = x0 + (t - lo) / (hi - lo) * (x1 - x0)
        body += [f'<line x1="{x:.1f}" y1="{y}" x2="{x:.1f}" y2="{y + 4}" stroke="{MUTED}"/>',
                 text(x, y + 16, fmt(t), 11, "middle", MUTED)]
    body.append(text((x0 + x1) / 2, y + 32, label, 11, "middle", MUTED))
    return body


def strips(x0, x1, top, rows, lo, hi, row_h=34):
    """rows: (label, values, colour). One row of points each, a median tick, values past hi counted."""
    body = []
    scale = lambda v: x0 + (min(max(v, lo), hi) - lo) / (hi - lo) * (x1 - x0)  # noqa: E731
    for i, (label, values, colour) in enumerate(rows):
        y = top + i * row_h
        body.append(f'<line x1="{x0}" y1="{y + row_h / 2}" x2="{x1}" y2="{y + row_h / 2}" stroke="{GRID}"/>')
        body.append(text(x0 - 8, y + row_h / 2 + 4, label, 12, "end"))
        for k, v in enumerate(values):
            jy = y + 6 + ((k * 0.6180339887) % 1) * (row_h - 12)
            body.append(f'<circle cx="{scale(v):.1f}" cy="{jy:.1f}" r="2" fill="{colour}" fill-opacity="0.45"/>')
        if values:
            m = statistics.median(values)
            body.append(f'<line x1="{scale(m):.1f}" y1="{y + 2}" x2="{scale(m):.1f}" y2="{y + row_h - 2}" '
                        f'stroke="{INK}" stroke-width="2"/>')
            over = sum(v > hi for v in values)
            note = f"n {len(values)}, median {m:.3g}" + (f"; {over} beyond the axis" if over else "")
            body.append(text(x1 + 8, y + row_h / 2 + 4, note, 11, "start", MUTED))
    return body


def order_to_failure(p10: Path) -> str:
    """Two real pairs at deadline 7.5 s (one per bit value) and one pair from the deadline-12 control."""
    def pairs(arm):
        traces = sorted((r["trace"] for r in jsonl(p10 / f"{arm}.endpoint.jsonl") if "trace" in r),
                        key=lambda t: t["cid"])
        results = {r["trace"]["cid"]: r["result"] for r in jsonl(p10 / f"{arm}.endpoint.jsonl") if "trace" in r}
        bits = (p10 / f"{arm}.bits").read_text().strip()
        return [(traces[2 * k], traces[2 * k + 1], results, bits[k]) for k in range(len(traces) // 2)]

    tight = pairs("p10_orderfail")
    chosen = [next(p for p in tight if p[3] == "0"), next(p for p in tight if p[3] == "1"),
              pairs("p10_orderfail_wide")[0]]
    titles = ["Deadline 7.5 s, bit 0: the server sends answer 1 first, so request 2 runs out of time",
              "Deadline 7.5 s, bit 1: the server sends answer 2 first, so request 1 runs out of time",
              "Deadline 12 s (control): both checks fit, whatever the order"]
    W, x0, x1, lo, hi = 800, 120, 640, 0.0, 12.5
    sx = lambda s: x0 + (s - lo) / (hi - lo) * (x1 - x0)  # noqa: E731
    body, y = [text(20, 24, "Order turned into failures: real request pairs from protocol 10", 14, weight="bold")], 44
    for (a, b, results, _), title in zip(chosen, titles):
        body.append(text(20, y + 14, title, 12, weight="bold"))
        t0 = min(a["admit"], b["admit"])
        for n, t in enumerate((a, b)):
            ly = y + 30 + n * 26
            s = lambda ns: (ns - t0) / 1e9  # noqa: E731
            ok = results[t["cid"]] == "accept"
            body.append(text(x0 - 8, ly + 4, f"request {n + 1}", 12, "end"))
            body.append(f'<line x1="{sx(s(t["admit"])):.1f}" y1="{ly}" x2="{sx(s(t["target"])):.1f}" y2="{ly}" '
                        f'stroke="{GRID}" stroke-width="8"/>')
            end = t["check_end"] if t["check_end"] is not None else t["target"]
            body.append(f'<rect x="{sx(s(t["check_start"])):.1f}" y="{ly - 4}" '
                        f'width="{max(sx(s(end)) - sx(s(t["check_start"])), 1):.1f}" height="8" '
                        f'fill="{OK if ok else BAD}" fill-opacity="{0.85 if ok else 0.35}"/>')
            body.append(f'<circle cx="{sx(s(t["answer"])):.1f}" cy="{ly}" r="4" fill="white" stroke="{INK}"/>')
            tx = sx(s(t["target"]))
            body.append(f'<line x1="{tx:.1f}" y1="{ly - 9}" x2="{tx:.1f}" y2="{ly + 9}" stroke="{INK}" stroke-width="2"/>')
            body.append(text(x1 + 10, ly + 4, "ok" if ok else "unavailable", 12, "start",
                             OK if ok else BAD, "bold"))
        y += 30 + 2 * 26 + 18
    body += axis(x0, x1, y, lo, hi, "seconds after the pair was admitted")
    y += 48
    legend = [("circle", "answer arrives (valid proof)"), ("ok", "check that passed"),
              ("bad", "check killed at the deadline"), ("tick", "release time: admission + deadline")]
    lx = 20
    for kind, label in legend:
        if kind == "circle":
            body.append(f'<circle cx="{lx + 6}" cy="{y}" r="4" fill="white" stroke="{INK}"/>')
        elif kind == "tick":
            body.append(f'<line x1="{lx + 6}" y1="{y - 8}" x2="{lx + 6}" y2="{y + 8}" stroke="{INK}" stroke-width="2"/>')
        else:
            body.append(f'<rect x="{lx}" y="{y - 4}" width="14" height="8" fill="{OK if kind == "ok" else BAD}" '
                        f'fill-opacity="{0.85 if kind == "ok" else 0.35}"/>')
        body.append(text(lx + 20, y + 4, label, 11, color=MUTED))
        lx += 30 + 6.2 * len(label)
    body.append(text(20, y + 26, "The grey band is each request's wait from admission to release. One checker takes "
                     "answers in arrival order.", 11, color=MUTED))
    return svg(W, y + 40, body)


def protocol10_arms(p10: Path) -> str:
    arms = [("p10_order", "Order code, batches of 3, D 20"), ("p10_orderfail", "Order to failure, D 7.5"),
            ("p10_orderfail_wide", "Same code, D 12 (control)"), ("p10_orderfail_cap", "D 7.5 with the cap"),
            ("p10_pending1", "Honest pairs, one pending")]
    kinds = [("accept", "ok", OK), ("deadline", "deadline", BAD), ("capped", "capped by the cap", "#d9822b"),
             ("busy", "busy (turned away)", "#7a7a7a")]
    analysis = json.loads((p10 / "protocol10_analysis.json").read_text())
    W, x0, x1, top, bar = 820, 220, 600, 50, 22
    body = [text(20, 24, "Protocol 10: what happened to every reply, per arm", 14, weight="bold")]
    for i, (arm, label) in enumerate(arms):
        counts = {}
        for r in jsonl(p10 / f"{arm}.endpoint.jsonl"):
            if r.get("result") in dict((k, 0) for k, _, _ in kinds):
                counts[r["result"]] = counts.get(r["result"], 0) + 1
        total = sum(counts.values())
        y = top + i * (bar + 18)
        body.append(text(x0 - 8, y + bar / 2 + 4, label, 12, "end"))
        x = x0
        for k, _, colour in kinds:
            n = counts.get(k, 0)
            w = n / total * (x1 - x0)
            if n:
                body.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{bar}" fill="{colour}" fill-opacity="0.8"/>')
                if w > 26:
                    body.append(text(x + w / 2, y + bar / 2 + 4, str(n), 11, "middle", "white", "bold"))
            x += w
        a = analysis.get(arm, {})
        if arm == "p10_orderfail_wide":
            note = "no failures, nothing to read"
        elif "decoded_right" in a and arm != "p10_order":
            note = f"{a['decoded_right']} of {a['bits']} bits read" + (f", {a['erased']} erased" if a.get("erased") else "")
        elif arm == "p10_order":
            note = f"{a['out_of_admission_order']} of {a['batches']} batches out of order"
        else:
            note = "no bits planted"
        body.append(text(x1 + 10, y + bar / 2 + 4, note, 11, color=MUTED))
    y = top + len(arms) * (bar + 18) + 4
    lx = x0
    for _, label, colour in kinds:
        body.append(f'<rect x="{lx}" y="{y}" width="12" height="12" fill="{colour}" fill-opacity="0.8"/>')
        body.append(text(lx + 16, y + 10, label, 11, color=MUTED))
        lx += 28 + 6.2 * len(label)
    body.append(text(20, y + 34, "Bars show each arm's replies as fractions; numbers are counts. D is the deadline in "
                     "seconds; the cap is W 8, F 2, C 8.", 11, color=MUTED))
    return svg(W, y + 48, body)


def timing_p9(p9: Path) -> str:
    def by_bit(trace, bits, offset_ms, scale):
        rows = jsonl(trace)
        return ([(r["elapsed_ms"] - offset_ms) / scale for r, b in zip(rows, bits) if b == "1"],
                [(r["elapsed_ms"] - offset_ms) / scale for r, b in zip(rows, bits) if b == "0"])

    ab = (p9 / "p9_arrival.bits").read_text().strip()
    a1, a0 = by_bit(p9 / "p9_arrival.trace.jsonl", ab, 0, 1000)
    tb = (p9 / "T.bits").read_text().strip()
    t1, t0 = by_bit(p9 / "T.jsonl", tb, 10000, 1)
    hb = (p9 / "H.bits").read_text().strip()
    h1, h0 = by_bit(p9 / "H.jsonl", hb, 10000, 1)
    W, x0, x1 = 800, 210, 560
    body = [text(20, 24, "Timing: the same 1 s code with and without fixed release (protocol 9)", 14, weight="bold"),
            text(20, 50, "A. Release on arrival (positive control, 64 replies): reply time in seconds", 12, weight="bold")]
    lo_a = min(a1 + a0) - 0.2
    hi_a = max(a1 + a0) + 0.2
    body += strips(x0, x1, 62, [("bit 1 (server waits)", a1, ONE), ("bit 0", a0, ZERO)], lo_a, hi_a)
    body += axis(x0, x1, 136, lo_a, hi_a, "seconds from question to reply")
    allv = sorted(t1 + t0 + h1 + h0)
    hi_b = allv[int(0.98 * (len(allv) - 1))]
    lo_b = min(0.0, allv[0])
    body.append(text(20, 200, "B. Fixed release, deadline 10 s (512 code and 512 honest replies): milliseconds after "
                     "the deadline", 12, weight="bold"))
    body += strips(x0, x1, 212, [("code, bit 1", t1, ONE), ("code, bit 0", t0, ZERO),
                                 ("honest, label 1", h1, ONE), ("honest, label 0", h0, ZERO)], lo_b, hi_b)
    body += axis(x0, x1, 352, lo_b, hi_b, "client reply time minus 10 000 ms")
    body.append(text(20, 404, "In A the server's wait shows directly. In B both bit values land in the same "
                     "sub-millisecond band; the registered decoder", 11, color=MUTED))
    body.append(text(20, 418, "did not detect the code (book: Measurements, protocol 9). Axis B ends at the 98th "
                     "percentile.", 11, color=MUTED))
    return svg(W, 432, body)


def load_p11(p11: Path) -> str:
    def late(arms):
        pairs = []
        for arm in arms:
            pairs += lateness(jsonl(p11 / f"{arm}.endpoint.jsonl"), (p11 / f"{arm}.bits").read_text().strip())
        return [x for x, b in pairs if b == "1"], [x for x, b in pairs if b == "0"]

    s1, s0 = late(["p11_S1", "p11_S2"])
    h1, h0 = late(["p11_H1", "p11_H2"])
    W, x0, x1, lo, hi = 800, 250, 560, 0.0, 0.6
    body = [text(20, 24, "Load near a release: how late each reply left, on the endpoint's clock (protocol 11)",
                 14, weight="bold")]
    body += strips(x0, x1, 44, [("straddle, bit 1 (check running)", s1, ONE), ("straddle, bit 0 (no check)", s0, ZERO),
                                ("honest, label 1", h1, ONE), ("honest, label 0", h0, ZERO)], lo, hi)
    body += axis(x0, x1, 184, lo, hi, "release lateness: sent minus target, ms")
    body.append(text(20, 236, "Bit 1 times the next answer so its check is running at this reply's release; bit 0 "
                     "starts it just after.", 11, color=MUTED))
    body.append(text(20, 250, "The client decoded the code from its own reply times (book: Measurements, protocol 11). "
                     "The laptop wasn't idle during this run.", 11, color=MUTED))
    return svg(W, 264, body)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p9", type=Path, required=True)
    ap.add_argument("--p10", type=Path, required=True)
    ap.add_argument("--p11", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    for name, content in (("order-to-failure.svg", order_to_failure(a.p10)), ("protocol10-arms.svg", protocol10_arms(a.p10)),
                          ("timing-p9.svg", timing_p9(a.p9)), ("load-p11.svg", load_p11(a.p11))):
        (a.out / name).write_text(content)
        print(a.out / name)


if __name__ == "__main__":
    main()
