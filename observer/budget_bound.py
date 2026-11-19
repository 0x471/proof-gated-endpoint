"""Failure budget over an epoch: what it allows, what it costs, and the floor no policy can beat.

A model, not a device measurement. The endpoint does not implement this policy.

The policy: count the server-caused failures in each epoch of N admitted replies. Up to K pass as
the server chose. The (K+1)th trips the budget: every later reply in that epoch is unavailable,
whatever the server does. The next epoch starts empty.

What is counted, as in observer/cap_bound.py: the ok/unavailable status of admitted replies, in the
order they leave. Not counted: timing, busy or halted answers, ids, missing replies, logs, resets.

Three results:
- count_patterns: an epoch allows exactly sum(C(N, j), j <= K+1) status strings, so log2 of that is
  an upper bound on the bits one epoch can carry, for any encoder.
- encode/decode: a code that reaches sum(C(N, j), j <= K) of them without ever tripping, so the
  bound is tight to within the strings that trip.
- floor_bits: any filter that leaves an honest epoch untouched with probability at least 1 - eps
  must allow at least this many strings, because it has to pass the likely honest fault patterns
  and a hacked server can imitate any of them. As N grows the rate tends to the binary entropy of
  the honest fault rate p. A policy can only get below it by dropping honest service.
- key_floor_replies: the same argument for a small secret. Any filter that leaves an honest run of n
  replies untouched lets a key of that many bits out within n replies, so no setting can delay a key
  beyond this point. Long epochs lower the long-run rate but allow a burst at the start of an epoch,
  so the setting that is best for a key is not the one that is best for the long-run rate.

Honest faults are modelled as independent with probability p. Real faults come in bursts.

    python observer/budget_bound.py
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cap_bound import bits as cap_bits  # noqa: E402
from cap_bound import count_patterns as cap_count  # noqa: E402
from cap_curves import honest_cost as cap_honest_cost  # noqa: E402
from cap_model import START, step  # noqa: E402

EPOCHS = [1 << k for k in range(6, 17)]
TABLE_EPOCHS = [64, 256, 1024, 4096, 16384, 65536]
DEVICE_CAP = (64, 1, 64)
KEY_BITS = 256
KEY_EPS = 0.001


def check(epoch: int, budget: int) -> None:
    if type(epoch) is not int or epoch < 1:
        raise ValueError("epoch must be a positive integer")
    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a non-negative integer")


def check_p(p: float) -> None:
    if not 0 <= p <= 1:
        raise ValueError("p must be a probability")


def run_epoch(epoch: int, budget: int, chosen: tuple[bool, ...]) -> tuple[bool, ...]:
    """The reference automaton: what the outsider sees (True = unavailable) for the server's choice."""
    check(epoch, budget)
    if len(chosen) > epoch:
        raise ValueError("more replies than the epoch holds")
    seen, failures, tripped = [], 0, False
    for failed in chosen:
        if tripped:
            seen.append(True)
            continue
        seen.append(failed)
        failures += failed
        tripped = failures > budget
    return tuple(seen)


def at_most(n: int, k: int) -> int:
    """Strings of length n with at most k failures."""
    total, term = 0, 1
    for j in range(min(n, k) + 1):
        total += term
        term = term * (n - j) // (j + 1)
    return total


def count_patterns(epoch: int, budget: int) -> int:
    """Distinct status strings one epoch allows, whatever the server does."""
    check(epoch, budget)
    return at_most(epoch, budget + 1)


def bits_per_reply(epoch: int, budget: int) -> float:
    return math.log2(count_patterns(epoch, budget)) / epoch


def encode(value: int, epoch: int, budget: int) -> tuple[bool, ...]:
    """The server's failure pattern for value, using at most budget failures, so it never trips."""
    check(epoch, budget)
    if not 0 <= value < at_most(epoch, budget):
        raise ValueError("value does not fit this epoch")
    out, left = [], budget
    for i in range(epoch):
        zero_branch = at_most(epoch - i - 1, left)
        if value < zero_branch:
            out.append(False)
        else:
            value -= zero_branch
            out.append(True)
            left -= 1
    return tuple(out)


def decode(seen: tuple[bool, ...], budget: int) -> int:
    """The outsider's reading of one untripped epoch."""
    value, left = 0, budget
    for i, failed in enumerate(seen):
        if failed:
            value += at_most(len(seen) - i - 1, left)
            left -= 1
    return value


def honest_outcome(epoch: int, budget: int, p: float) -> tuple[float, float]:
    """(chance an honest epoch trips, expected share of its replies lost to the lockout)."""
    check(epoch, budget)
    check_p(p)
    first = budget + 1
    if p == 0 or first > epoch:
        return 0.0, 0.0
    if p == 1:
        return 1.0, (epoch - first) / epoch
    # The (K+1)th failure lands on reply t with the negative binomial chance, built up term by term.
    log_q = first * math.log(p)
    log_ok = math.log1p(-p)
    trip = locked = 0.0
    for t in range(first, epoch + 1):
        q = math.exp(log_q)
        trip += q
        locked += q * (epoch - t)
        log_q += math.log(t) - math.log(t - budget) + log_ok
    return min(trip, 1.0), locked / epoch


def entropy(p: float) -> float:
    """Binary entropy in bits: the limit of the floor, per reply."""
    check_p(p)
    if p in (0, 1):
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def floor_bits(epoch: int, p: float, eps: float) -> float:
    """log2 of the fewest strings any filter must pass to leave an honest epoch untouched w.p. >= 1-eps.

    The smallest such set takes the likeliest strings first, which for p < 1/2 means fewest failures
    first. Computed in floating point.
    """
    if type(epoch) is not int or epoch < 1:
        raise ValueError("epoch must be a positive integer")
    if not 0 <= p < 0.5:
        raise ValueError("p must be in [0, 1/2)")
    if not 0 < eps < 1:
        raise ValueError("eps must be in (0, 1)")
    if p == 0:
        return 0.0
    need = 1 - eps
    total, term, covered = 0, 1, 0.0
    log_string = epoch * math.log1p(-p)
    odds = math.log(p) - math.log1p(-p)
    for j in range(epoch + 1):
        share = math.exp(math.log(term) + log_string + j * odds)
        if covered + share >= need:
            fraction = (need - covered) / share
            total += max(1, math.ceil(fraction * term)) if term < 2**53 else (term * int(fraction * 2**53) >> 53) + 1
            return math.log2(total)
        covered += share
        total += term
        term = term * (epoch - j) // (j + 1)
    return math.log2(total)


def replies_for(target_bits: float, epoch: int, budget: int) -> int:
    """Fewest replies from an empty budget after which the statuses could carry target_bits."""
    check(epoch, budget)
    whole = math.log2(count_patterns(epoch, budget))
    if whole == 0:
        raise ValueError("this policy carries nothing")
    full = max(0, math.ceil(target_bits / whole) - 1)
    missing = target_bits - full * whole
    low, high = 0, epoch
    while low < high:
        mid = (low + high) // 2
        if math.log2(at_most(mid, budget + 1)) >= missing:
            high = mid
        else:
            low = mid + 1
    return full * epoch + low


def cap_replies_for(target_bits: float, window: int, failures: int, cooldown: int) -> int:
    """The same for the sliding-window cap, from cap_bound's exact count."""
    low, high = 1, 1
    while math.log2(cap_count(window, failures, cooldown, high)) < target_bits:
        low, high = high, high * 2
    while low < high:
        mid = (low + high) // 2
        if math.log2(cap_count(window, failures, cooldown, mid)) >= target_bits:
            high = mid
        else:
            low = mid + 1
    return low


def key_floor_replies(target_bits: float, p: float, eps: float) -> int:
    """The first n at which any filter that spares an honest run of n replies w.p. >= 1-eps allows target_bits."""
    if p == 0:
        raise ValueError("with no honest faults the floor is zero")
    low, high = 1, 2
    while floor_bits(high, p, eps) < target_bits:
        low, high = high, high * 2
    while low < high:
        mid = (low + high) // 2
        if floor_bits(mid, p, eps) >= target_bits:
            high = mid
        else:
            low = mid + 1
    return low


def run_survives(epoch: int, budget: int, p: float, replies: int) -> float:
    """Chance an honest run of this many replies never trips the budget."""
    return (1 - honest_outcome(epoch, budget, p)[0]) ** math.ceil(replies / epoch)


def key_budget(epoch: int, p: float, target_bits: float, eps: float) -> int:
    """The tightest budget that spares the honest run up to the point a key could be out, w.p. >= 1-eps."""
    low, high = 0, epoch
    while low < high:
        mid = (low + high) // 2
        if run_survives(epoch, mid, p, replies_for(target_bits, epoch, mid)) >= 1 - eps:
            high = mid
        else:
            low = mid + 1
    return low


def best_for_key(p: float, eps: float = KEY_EPS, target_bits: float = KEY_BITS,
                 epochs: list[int] | None = None) -> tuple[int, int, int]:
    """(epoch, budget, replies) that delays a key longest among epochs, under the same promise as the limit."""
    options = []
    for epoch in epochs or EPOCHS:
        budget = key_budget(epoch, p, target_bits, eps)
        options.append((replies_for(target_bits, epoch, budget), epoch, budget))
    replies, epoch, budget = max(options)
    return epoch, budget, replies


def cap_trip_chance(window: int, failures: int, cooldown: int, n: int, p: float) -> float:
    """Chance the sliding-window cap trips at least once in n honest replies, from an empty cap."""
    check_p(p)
    states = {START: 1.0}
    tripped = 0.0
    for _ in range(n):
        nxt: dict = defaultdict(float)
        for s, q in states.items():
            for failed, w in ((False, 1 - p), (True, p)):
                if w == 0:
                    continue
                s2, out = step(s, window, failures, cooldown, failed)
                if out == "trip":
                    tripped += q * w
                else:
                    nxt[s2] += q * w
        states = nxt
    return tripped


def duration(seconds: float) -> str:
    for size, unit in ((86400, "days"), (3600, "h"), (60, "min")):
        if seconds >= size:
            return f"{seconds / size:.1f} {unit}"
    return f"{seconds:.1f} s"


def epoch_table(p: float, max_trip: float) -> list[str]:
    rows = [f"Honest fault rate p = {p}; budget set so an honest epoch trips with chance at most {max_trip}.",
            "",
            "| Epoch N | Budget K | Chance an honest epoch trips | Honest replies lost | Long-run bits per reply, at most | "
            f"Floor for this N | Limit H(p) | Replies before a {KEY_BITS}-bit key could get out |",
            "|---|---|---|---|---|---|---|---|"]
    for epoch in TABLE_EPOCHS:
        budget = next(k for k in range(epoch + 1) if honest_outcome(epoch, k, p)[0] <= max_trip)
        trip, loss = honest_outcome(epoch, budget, p)
        rows.append(f"| {epoch} | {budget} | {trip:.2e} | {100 * loss:.4f} % | {bits_per_reply(epoch, budget):.4f} | "
                    f"{floor_bits(epoch, p, max_trip) / epoch:.4f} | {entropy(p):.4f} | "
                    f"{replies_for(KEY_BITS, epoch, budget):,} |")
    return rows


def key_table(rates: list[float]) -> list[str]:
    w, f, c = DEVICE_CAP
    cap_n = cap_replies_for(KEY_BITS, w, f, c)
    rows = [f"Replies before a {KEY_BITS}-bit key could get out through which replies fail. The budget and the limit both "
            f"promise that an honest run of that length is left untouched with chance at least {1 - KEY_EPS}.",
            "",
            f"| Honest fault rate | Cap {w}/{f}/{c}: replies | Chance the cap trips on an honest run that long | "
            "Best budget (N, K) | Budget: replies | Budget: honest replies lost | Limit for any policy: replies | "
            "Limit at 900 replies/h | Limit at 1,000 replies/s |",
            "|---|---|---|---|---|---|---|---|---|"]
    for p in rates:
        epoch, budget, n = best_for_key(p)
        loss = honest_outcome(epoch, budget, p)[1]
        limit = key_floor_replies(KEY_BITS, p, KEY_EPS)
        rows.append(f"| {p:g} | {cap_n:,} | {100 * cap_trip_chance(w, f, c, cap_n, p):.2f} % | ({epoch}, {budget}) | {n:,} | "
                    f"{100 * loss:.4f} % | {limit:,} | {duration(limit / 900 * 3600)} | {duration(limit / 1000)} |")
    return rows


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p", type=float, nargs="+", default=[0.01, 0.001, 0.0001, 0.00001])
    ap.add_argument("--epoch-p", type=float, default=0.001)
    ap.add_argument("--max-trip", type=float, default=0.001)
    a = ap.parse_args(argv)
    print("\n".join(key_table(a.p)))
    print()
    print("\n".join(epoch_table(a.epoch_p, a.max_trip)))


if __name__ == "__main__":
    main()
