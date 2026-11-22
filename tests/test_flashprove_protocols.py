import os
from pathlib import Path
import subprocess

import pytest

from conftest import ROOT


def runner(tmp_path):
    device = tmp_path / "device"
    device.mkdir()
    script = device / "flashprove_protocols.sh"
    script.write_text((ROOT / "device/flashprove_protocols.sh").read_text())
    scenario = device / "flashprove_scenario.sh"
    scenario.write_text('#!/usr/bin/env bash\ncat >/dev/null\nprintf "%s\\n" "$1" >> "$OUT/calls"\n')
    scenario.chmod(0o755)
    return script


@pytest.mark.parametrize("arms", ["typo", "p10_order typo", "p9_T1", "   "])
def test_protocol_runner_refuses_unknown_or_empty_arm_selection(tmp_path, arms):
    script = runner(tmp_path)
    out = tmp_path / "out"
    result = subprocess.run(["bash", str(script), "10"], env={**os.environ, "OUT": str(out), "ARMS": arms},
                            capture_output=True, text=True)
    assert result.returncode == 2 and not (out / "calls").exists()


def test_protocol_runner_keeps_registered_order_when_child_reads_stdin(tmp_path):
    script = runner(tmp_path)
    out = tmp_path / "out"
    result = subprocess.run(["bash", str(script), "10"],
                            env={**os.environ, "OUT": str(out), "ARMS": "p10_pending1 p10_order"},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (out / "calls").read_text().splitlines() == ["p10_order", "p10_pending1"]
    assert len((out / "p10_order.bits").read_text().strip()) == 128
