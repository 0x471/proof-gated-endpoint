import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "observer"))
import figures  # noqa: E402

RUNS = {"p9": ROOT / "runs/p9_20261121b", "p10": ROOT / "runs/p10_20261121", "p11": ROOT / "runs/p11_20261121"}


def test_ticks_cover_the_range_with_round_steps():
    assert figures.ticks(0, 12.5) == [0, 2.5, 5, 7.5, 10, 12.5]
    assert figures.ticks(0, 0.6) == [0, 0.2, 0.4, 0.6]


def test_strips_count_every_value_and_those_beyond_the_axis():
    body = "".join(figures.strips(100, 300, 0, [("row", [0.1, 0.2, 5.0], "#000")], 0, 1))
    assert body.count("<circle") == 3
    assert "n 3, median 0.2; 1 beyond the axis" in body


@pytest.mark.skipif(not all(p.exists() for p in RUNS.values()), reason="needs the local protocol 9-11 runs")
def test_all_figures_are_valid_svg(tmp_path):
    figures.main(["--p9", str(RUNS["p9"]), "--p10", str(RUNS["p10"]), "--p11", str(RUNS["p11"]), "--out", str(tmp_path)])
    names = sorted(p.name for p in tmp_path.glob("*.svg"))
    assert names == ["load-p11.svg", "order-to-failure.svg", "protocol10-arms.svg", "timing-p9.svg"]
    for p in tmp_path.glob("*.svg"):
        ET.parse(p)
