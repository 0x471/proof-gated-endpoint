import random
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


def env_runner(tmp_path):
    script = runner(tmp_path)
    (tmp_path / "device/flashprove_scenario.sh").write_text(
        '#!/usr/bin/env bash\ncat >/dev/null\n'
        'printf "%s cpu=%s sample=%s period=%s deadline=%s\\n" "$1" "${RELEASE_CPU-unset}" "${CPU_SAMPLE-unset}" '
        '"${PERIOD_MS-unset}" "$DEADLINE" >> "$OUT/calls"\n')
    return script


def stream(seed, n, skip):
    r = random.Random(seed)
    return "".join(r.choice("01") for _ in range(skip + n))[skip:]


def test_protocol_12_runs_six_interleaved_arms_with_registered_cpu_and_bits(tmp_path):
    script = env_runner(tmp_path)
    out = tmp_path / "out"
    result = subprocess.run(["bash", str(script), "12"], env={**os.environ, "OUT": str(out)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (out / "calls").read_text().splitlines() == [
        f"{name} cpu={cpu} sample=1 period=7000 deadline=10" for name, cpu in
        [("p12_SU1", "unset"), ("p12_SP1", "0"), ("p12_HP", "0"), ("p12_HU", "unset"), ("p12_SP2", "0"),
         ("p12_SU2", "unset")]]
    bits = {p.stem: p.read_text().strip() for p in out.glob("p12_*.bits")}
    assert bits["p12_SU1"] == bits["p12_HU"] == stream(121, 256, 0)
    assert bits["p12_SU2"] == stream(121, 256, 256)
    assert bits["p12_SP1"] == bits["p12_HP"] == stream(122, 256, 0)
    assert bits["p12_SP2"] == stream(122, 256, 256)
    for name in bits:
        text = (out / f"{name}.laptop.txt").read_text()
        assert text.startswith("before ") and "\nafter " in text


def test_earlier_protocols_keep_release_cpu_and_sampling_off(tmp_path):
    script = env_runner(tmp_path)
    out = tmp_path / "out"
    result = subprocess.run(["bash", str(script), "11"],
                            env={**os.environ, "OUT": str(out), "RELEASE_CPU": "3", "CPU_SAMPLE": ""},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = (out / "calls").read_text().splitlines()
    assert [c.split()[0] for c in calls] == ["p11_S1", "p11_H1", "p11_H2", "p11_S2"]
    assert all(" cpu=unset sample= " in c for c in calls)
    assert (out / "p11_S2.bits").read_text().strip() == stream(111, 256, 256)


@pytest.mark.parametrize("protocol, arm, seed, n, skip", [
    ("9", "p9_arrival", 92, 64, 0), ("9", "p9_T2", 91, 256, 256), ("10", "p10_orderfail_cap", 104, 128, 0),
    ("10", "p10_pending1", 105, 128, 0)])
def test_registered_seeds_still_parse_after_the_cpu_column(tmp_path, protocol, arm, seed, n, skip):
    script = env_runner(tmp_path)
    out = tmp_path / "out"
    result = subprocess.run(["bash", str(script), protocol], env={**os.environ, "OUT": str(out), "ARMS": arm},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (out / f"{arm}.bits").read_text().strip() == stream(seed, n, skip)
