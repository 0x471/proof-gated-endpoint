import os
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
    assert result.returncode == 2 and not out.exists()


@pytest.mark.parametrize("arms", ["p10_pending1 p10_order", "p10_pending1\tp10_order", "p10_pending1\np10_order"])
def test_protocol_runner_keeps_registered_order_when_child_reads_stdin(tmp_path, arms):
    script = runner(tmp_path)
    out = tmp_path / "out"
    result = subprocess.run(["bash", str(script), "10"],
                            env={**os.environ, "OUT": str(out), "ARMS": arms},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (out / "calls").read_text().splitlines() == ["p10_order", "p10_pending1"]
    assert len((out / "p10_order.bits").read_text().strip()) == 128


def test_protocol_repeats_preserve_attempts_with_the_same_timestamp(tmp_path):
    script = runner(tmp_path)
    out = tmp_path / "out"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    date = bin_dir / "date"
    date.write_text('#!/usr/bin/env bash\nprintf "20261121T151043Z\\n"\n')
    date.chmod(0o755)
    env = {**os.environ, "OUT": str(out), "ARMS": "p10_order", "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for text in ["first", "second", "current"]:
        result = subprocess.run(["bash", str(script), "10"], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        (out / "p10_order.trace.jsonl").write_text(text)
    attempts = list(out.glob("attempt_*"))
    assert len(attempts) == 2
    assert {(p / "p10_order.trace.jsonl").read_text() for p in attempts} == {"first", "second"}
    assert (out / "p10_order.trace.jsonl").read_text() == "current"
