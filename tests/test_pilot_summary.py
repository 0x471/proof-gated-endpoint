import random
import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "observer"))
from pilot_summary import binom_tail, power, threshold_decode  # noqa: E402


def test_binomial_tail():
    assert binom_tail(0, 10) == 1.0
    assert binom_tail(10, 10) == 2 ** -10
    assert abs(binom_tail(6, 10) - 386 / 1024) < 1e-12


def test_decoder_reads_a_separable_code_on_the_held_out_half():
    bits = "0110100111001010" * 4
    d = threshold_decode([10.0 + (5 if b == "1" else 0) for b in bits], bits)
    assert d["heldout_right"] == d["heldout_n"] == 32 and d["mean_diff_ms"] == 5


def test_simulated_power_holds_its_false_positive_rate_and_finds_a_large_shift():
    rng = random.Random(1)
    residuals = [rng.gauss(0, 1) for _ in range(64)]
    assert power(residuals, 256, 0.0, 600, random.Random(2)) <= 0.03
    assert power(residuals, 256, 3.0, 50, random.Random(3)) == 1.0
