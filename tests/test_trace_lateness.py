import random
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "observer"))
from trace_lateness import lateness, median_diff, permutation_p  # noqa: E402


def record(seq, late_ns, target=10_000_000_000):
    return {"seq": seq, "result": "accept", "trace": {"target": target, "sent": target + late_ns}}


def test_lateness_follows_sequence_order_and_skips_requests_without_a_bit():
    records = [{"result": "start"}, record(2, 200_000), record(1, 100_000), record(3, 300_000)]
    assert lateness(records, "10") == [(0.1, "1"), (0.2, "0")]


def test_lateness_refuses_more_bits_than_released_requests():
    with pytest.raises(ValueError):
        lateness([record(1, 0)], "10")


@pytest.mark.parametrize("seqs", [(1, 3), (1, 1), (2, 3)])
def test_lateness_refuses_missing_or_duplicate_sequences(seqs):
    with pytest.raises(ValueError):
        lateness([record(seq, 0) for seq in seqs], "10")


@pytest.mark.parametrize("bits", ["", "1x"])
def test_lateness_refuses_invalid_labels(bits):
    with pytest.raises(ValueError):
        lateness([record(1, 0), record(2, 0)], bits)


def test_lateness_refuses_a_sequence_for_the_wrong_client_request():
    r = record(1, 0)
    r["trace"]["cid"] = 1
    with pytest.raises(ValueError):
        lateness([r], "1")


def test_records_without_a_release_target_are_left_out():
    records = [record(1, 0), {"seq": 2, "result": "accept", "trace": {"target": None, "sent": 5}}]
    assert lateness(records, "1") == [(0.0, "1")]


def test_median_difference_and_permutation_p():
    # Continuous values, as nanosecond lateness is: with only two distinct values a median-difference
    # permutation test is too coarse to mean anything.
    rng = random.Random(0)
    pairs = [(1.0 + rng.gauss(0, 0.1), "1") for _ in range(20)] + [(rng.gauss(0, 0.1), "0") for _ in range(20)]
    assert 0.8 < median_diff(pairs) < 1.2
    assert permutation_p(pairs, 2000, random.Random(0)) < 0.01
    rng = random.Random(1)
    null = [(rng.gauss(0, 1), rng.choice("01")) for _ in range(200)]
    assert permutation_p(null, 2000, random.Random(2)) > 0.01
