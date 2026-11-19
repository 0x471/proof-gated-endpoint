import math
import random
import sys
from itertools import product
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "observer"))

from budget_bound import (  # noqa: E402
    at_most,
    best_for_key,
    bits_per_reply,
    cap_replies_for,
    cap_trip_chance,
    count_patterns,
    decode,
    encode,
    entropy,
    floor_bits,
    honest_outcome,
    key_budget,
    key_floor_replies,
    replies_for,
    run_epoch,
    run_survives,
)
from cap_bound import count_patterns as cap_count  # noqa: E402
from cap_model import START, step  # noqa: E402


def seen_strings(epoch, budget):
    return {run_epoch(epoch, budget, c) for c in product((False, True), repeat=epoch)}


@pytest.mark.parametrize("epoch,budget", [(n, k) for n in range(1, 11) for k in range(0, 12)])
def test_count_matches_every_choice_the_server_could_make(epoch, budget):
    assert count_patterns(epoch, budget) == len(seen_strings(epoch, budget))


def test_nothing_after_a_trip_depends_on_the_server():
    chosen_a = (True, True, False, False, True)
    chosen_b = (True, True, True, False, False)
    assert run_epoch(5, 1, chosen_a) == run_epoch(5, 1, chosen_b) == (True, True, True, True, True)


def test_a_budget_as_large_as_the_epoch_is_no_limit():
    assert count_patterns(8, 8) == 2**8
    assert bits_per_reply(8, 8) == 1.0


def test_zero_budget_carries_only_the_position_of_the_first_failure():
    assert count_patterns(16, 0) == 17


@pytest.mark.parametrize("epoch,budget", [(1, 0), (6, 0), (6, 2), (9, 3), (10, 10)])
def test_code_round_trips_through_the_policy_without_tripping(epoch, budget):
    for value in range(at_most(epoch, budget)):
        chosen = encode(value, epoch, budget)
        assert sum(chosen) <= budget
        assert run_epoch(epoch, budget, chosen) == chosen
        assert decode(chosen, budget) == value


def test_code_reaches_every_untripped_string():
    epoch, budget = 9, 3
    strings = {encode(v, epoch, budget) for v in range(at_most(epoch, budget))}
    assert len(strings) == at_most(epoch, budget)
    assert strings == {c for c in product((False, True), repeat=epoch) if sum(c) <= budget}


def test_code_works_at_a_realistic_size():
    epoch, budget = 4096, 12
    rng = random.Random(11)
    for _ in range(50):
        value = rng.randrange(at_most(epoch, budget))
        assert decode(run_epoch(epoch, budget, encode(value, epoch, budget)), budget) == value


def test_a_value_too_large_is_refused():
    with pytest.raises(ValueError):
        encode(at_most(6, 1), 6, 1)


def test_honest_outcome_against_enumeration():
    epoch, budget, p = 10, 2, 0.17
    trip = lost = 0.0
    for chosen in product((False, True), repeat=epoch):
        weight = p ** sum(chosen) * (1 - p) ** (epoch - sum(chosen))
        failures = 0
        for i, failed in enumerate(chosen):
            failures += failed
            if failures > budget:
                trip += weight
                lost += weight * (epoch - i - 1)
                break
    got_trip, got_loss = honest_outcome(epoch, budget, p)
    assert got_trip == pytest.approx(trip, abs=1e-12)
    assert got_loss == pytest.approx(lost / epoch, abs=1e-12)


def test_honest_outcome_edges():
    assert honest_outcome(64, 1, 0.0) == (0.0, 0.0)
    assert honest_outcome(8, 8, 0.5) == (0.0, 0.0)
    assert honest_outcome(8, 2, 1.0) == (1.0, 5 / 8)


def test_bad_probability_is_refused():
    with pytest.raises(ValueError):
        honest_outcome(8, 1, 1.5)


def test_entropy_values():
    assert entropy(0.5) == 1.0
    assert entropy(0.0) == entropy(1.0) == 0.0
    assert entropy(0.11) == pytest.approx(0.49992, abs=1e-4)


def smallest_set_by_enumeration(epoch, p, eps):
    weights = sorted((p ** sum(c) * (1 - p) ** (epoch - sum(c)) for c in product((0, 1), repeat=epoch)), reverse=True)
    covered, size = 0.0, 0
    for w in weights:
        covered += w
        size += 1
        if covered >= 1 - eps:
            return size
    return size


@pytest.mark.parametrize("epoch,p,eps", [(8, 0.1, 0.2), (10, 0.05, 0.1), (12, 0.2, 0.3), (12, 0.01, 0.05)])
def test_floor_is_the_smallest_possible_set(epoch, p, eps):
    assert 2 ** floor_bits(epoch, p, eps) == pytest.approx(smallest_set_by_enumeration(epoch, p, eps), abs=1e-6)


def test_floor_tends_to_the_entropy():
    p = 0.01
    assert floor_bits(1 << 20, p, 0.01) / (1 << 20) == pytest.approx(entropy(p), rel=0.03)


def test_no_fault_rate_needs_no_strings_beyond_all_ok():
    assert floor_bits(64, 0.0, 0.01) == 0.0


@pytest.mark.parametrize("epoch,p,eps", [(1024, 0.001, 0.001), (4096, 0.01, 0.001), (16384, 0.0001, 0.01)])
def test_the_budget_never_allows_fewer_strings_than_the_floor(epoch, p, eps):
    k = next(k for k in range(epoch + 1) if honest_outcome(epoch, k, p)[0] <= eps)
    assert math.log2(count_patterns(epoch, k)) >= floor_bits(epoch, p, eps) - 1e-9


@pytest.mark.parametrize("target", [1, 8, 17, 40, 100])
def test_replies_for_is_the_first_point_that_reaches_the_target(target):
    epoch, budget = 32, 1
    n = replies_for(target, epoch, budget)
    def capacity(m):
        whole, rest = divmod(m, epoch)
        return whole * math.log2(count_patterns(epoch, budget)) + math.log2(at_most(rest, budget + 1))
    assert capacity(n) >= target
    assert capacity(n - 1) < target


def test_cap_replies_matches_the_published_key_table():
    # book/src/results.md, "From a per-reply bound to time".
    assert cap_replies_for(256, 64, 1, 64) == 2200
    assert cap_replies_for(2048, 64, 1, 64) == 17879
    assert math.log2(cap_count(64, 1, 64, 2200)) >= 256


@pytest.mark.parametrize("p,eps", [(0.01, 0.001), (0.001, 0.001), (0.0001, 0.01)])
def test_key_floor_is_the_first_length_that_reaches_the_key(p, eps):
    n = key_floor_replies(256, p, eps)
    assert floor_bits(n, p, eps) >= 256
    assert floor_bits(n - 1, p, eps) < 256


def test_key_floor_needs_a_fault_rate():
    with pytest.raises(ValueError):
        key_floor_replies(256, 0.0, 0.001)


def test_floor_never_shrinks_with_more_replies():
    values = [floor_bits(n, 0.02, 0.01) for n in range(1, 400)]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:]))


@pytest.mark.parametrize("epoch,budget,p,epochs", [(256, 2, 0.001, 4), (1024, 5, 0.001, 3), (64, 1, 0.005, 8)])
def test_a_run_of_epochs_still_allows_at_least_the_floor(epoch, budget, p, epochs):
    survive = (1 - honest_outcome(epoch, budget, p)[0]) ** epochs
    allowed = epochs * math.log2(count_patterns(epoch, budget))
    assert allowed >= floor_bits(epoch * epochs, p, 1 - survive) - 1e-9


def test_a_long_epoch_lets_a_key_out_in_a_burst():
    # Tuned for the long-run rate, this setting is worse for a key than the device's cap (2,200 replies).
    assert replies_for(256, 65536, 78) < 2200


@pytest.mark.parametrize("p", [0.01, 0.001, 0.0001])
def test_the_best_budget_keeps_the_promise_and_never_beats_the_limit(p):
    eps = 0.001
    epoch, budget, replies = best_for_key(p, eps)
    assert run_survives(epoch, budget, p, replies) >= 1 - eps
    assert replies <= key_floor_replies(256, p, eps)


def test_key_budget_is_the_tightest_that_keeps_the_promise():
    epoch, p, eps = 4096, 0.001, 0.001
    k = key_budget(epoch, p, 256, eps)
    assert run_survives(epoch, k, p, replies_for(256, epoch, k)) >= 1 - eps
    assert run_survives(epoch, k - 1, p, replies_for(256, epoch, k - 1)) < 1 - eps


def test_cap_trip_chance_by_hand():
    # With no failure allowed, the first failure trips.
    assert cap_trip_chance(4, 0, 4, 50, 0.02) == pytest.approx(1 - 0.98**50, abs=1e-12)
    assert cap_trip_chance(64, 1, 64, 100, 0.0) == 0.0


def test_cap_trip_chance_agrees_with_simulation():
    rng = random.Random(5)
    runs = 20000
    hits = 0
    for _ in range(runs):
        s = START
        for _ in range(200):
            s, out = step(s, 16, 1, 16, rng.random() < 0.01)
            if out == "trip":
                hits += 1
                break
    assert cap_trip_chance(16, 1, 16, 200, 0.01) == pytest.approx(hits / runs, abs=0.01)


def test_best_setting_is_the_one_that_delays_a_key_longest():
    p, eps, epochs = 0.001, 0.001, [256, 4096, 65536]
    _, _, replies = best_for_key(p, eps, 256, epochs)
    assert replies == max(replies_for(256, e, key_budget(e, p, 256, eps)) for e in epochs)
