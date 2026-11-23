import json
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "observer"))
import protocol12 as p12  # noqa: E402


def block(bits, admit_step=7_000_000_000, late_ns=None, client_extra=None):
    n = len(bits) + 1
    rows, records = [], []
    for j in range(n):
        admit = 1_000_000_000 + j * admit_step + (client_extra[j] if client_extra else 0)
        target = admit + 10_000_000_000
        sent = target + (late_ns[j] if late_ns else 100_000)
        trace = {"cid": j, "admit": admit, "answer": admit + 1, "check_start": admit + 2,
                 "check_end": admit + 3, "target": target, "sent": sent}
        records.append({"seq": j + 1, "result": "accept", "trace": trace})
        rows.append({"id": j, "elapsed_ms": (sent - admit) / 1e6 + 1.0, "answer": {"id": j, "ok": True}})
    return {"rows": rows, "records": records, "bits": bits, "traces": {r["trace"]["cid"]: r["trace"] for r in records}}


def manifest(name, **over):
    m = {"status": "ok", "release_cpu": "0" if name in p12.PINNED else "", "cpu_sample": True, "commit": "c",
         "endpoint_sha256": "e", "pi_before": ["boot_id b"], "pi_after": ["boot_id b"]}
    return {**m, **over}


def arms(**over):
    return {name: {"manifest": manifest(name, **over.get(name, {}))} for name in p12.ARMS}


@pytest.mark.parametrize("code, control, expected", [
    (0.005, 0.5, "leak shown"), (0.005, 0.04, "inconclusive"), (0.03, 0.5, "inconclusive"),
    (0.2, 0.01, "not detected")])
def test_decision_rule(code, control, expected):
    assert p12.verdict(code, control) == expected


@pytest.mark.parametrize("ci, expected", [((0.01, 0.2), "reduced"), ((-0.2, -0.01), "increased"),
                                          ((-0.01, 0.01), "no difference shown")])
def test_effect_needs_the_interval_to_exclude_zero(ci, expected):
    assert p12.effect_reading(ci) == expected


def test_validity_accepts_one_boot_one_build_and_pinning_only_in_p_arms():
    assert p12.validity(arms()) == []


@pytest.mark.parametrize("arm, field, value", [
    ("p12_SU1", "status", "failed"), ("p12_SP2", "release_cpu", ""), ("p12_HU", "release_cpu", "0"),
    ("p12_HP", "cpu_sample", False), ("p12_SU2", "commit", "other"), ("p12_SP1", "endpoint_sha256", "x"),
    ("p12_HU", "pi_after", ["boot_id other"]), ("p12_HP", "pi_before", [])])
def test_validity_refuses_any_broken_arm(arm, field, value):
    assert p12.validity(arms(**{arm: {field: value}}))


def test_validity_refuses_arms_from_two_boots():
    over = {n: {"pi_before": ["boot_id two"], "pi_after": ["boot_id two"]} for n in ("p12_SU2", "p12_SP2")}
    assert "the arms are not from one boot" in p12.validity(arms(**over))


def test_shifted_rows_label_row_j_with_bit_j_minus_shift():
    rows, bits = p12.shifted_rows(block("0110"), 2)
    assert bits == "011" and [r["id"] for r in rows] == [0, 1, 2]
    assert [r["answer"]["id"] for r in rows] == [0, 1, 2]
    assert rows[0]["elapsed_ms"] == block("0110")["rows"][2]["elapsed_ms"]


def test_admission_metrics_read_a_late_admission_from_both_clocks():
    bits = "0101" * 8
    extra = [0, 0] + [300_000 if b == "0" else 0 for b in bits]  # request j admitted late when bit j-2 is 0
    a = block(bits, client_extra=extra[: len(bits) + 1])
    for r, x in zip(a["rows"], extra):
        r["elapsed_ms"] += x / 1e6  # the client sees the late admission; the endpoint's admit-to-send doesn't
    ms = p12.admission_metrics(a, 2)
    d, _ = p12.median_perm([(m, l) for m, _, l in ms], 200)
    g, _ = p12.median_perm([(g, l) for _, g, l in ms if g is not None], 200)
    assert d == pytest.approx(-0.3) and g < 0


def test_bootstrap_difference_is_zero_for_identical_conditions_and_positive_when_p_is_flat():
    bits = "01" * 20
    leaky = block(bits, late_ns=[400_000 if b == "1" else 100_000 for b in bits] + [100_000])
    flat = block(bits)
    same = p12.boot_diff([p12.lateness_pairs(leaky)], [p12.lateness_pairs(leaky)], 200)
    assert same["diff"] == 0 and same["ci_diff"][0] <= 0 <= same["ci_diff"][1]
    less = p12.boot_diff([p12.lateness_pairs(leaky)], [p12.lateness_pairs(flat)], 200)
    assert less["diff"] == pytest.approx(0.3) and p12.effect_reading(less["ci_diff"]) == "reduced"


def test_load_refuses_a_block_with_a_missing_reply(tmp_path):
    a = block("01")
    (tmp_path / "x.trace.jsonl").write_text("".join(json.dumps(r) + "\n" for r in a["rows"][:-1]))
    (tmp_path / "x.endpoint.jsonl").write_text("".join(json.dumps(r) + "\n" for r in a["records"]))
    (tmp_path / "x.bits").write_text("01\n")
    (tmp_path / "x.manifest.json").write_text("{}")
    with pytest.raises(ValueError):
        p12.load(tmp_path, "x")
