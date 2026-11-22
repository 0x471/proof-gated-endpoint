import json
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "observer"))
from jitter_test import load_ms  # noqa: E402
from join_blocks import join  # noqa: E402
from protocol10 import order_result, orderfail_result, verdict  # noqa: E402


def rows(n, ms=10.0, ok=lambda i: True):
    return [{"id": i, "elapsed_ms": ms + i, "answer": {"id": i, "ok": ok(i)}} for i in range(n)]


def test_join_renumbers_blocks_in_run_order_and_jitter_test_accepts_the_result(tmp_path):
    out, bits = join([(rows(3), "101"), (rows(2, 50.0), "01")])
    assert [r["id"] for r in out] == [r["answer"]["id"] for r in out] == [0, 1, 2, 3, 4]
    assert [r["elapsed_ms"] for r in out] == [10.0, 11.0, 12.0, 50.0, 51.0] and bits == "10101"
    (tmp_path / "t.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
    assert load_ms(str(tmp_path / "t.jsonl"), bits) == [10.0, 11.0, 12.0, 50.0, 51.0]


def test_join_drops_each_blocks_last_row_for_the_straddle_code():
    out, bits = join([(rows(3), "10"), (rows(3), "01")], drop_last=True)
    assert len(out) == 4 and bits == "1001" and [r["id"] for r in out] == [0, 1, 2, 3]


@pytest.mark.parametrize("blocks", [[(rows(3), "10")], [(rows(3), "1x1")], [(rows(3)[::-1], "101")]])
def test_join_refuses_blocks_that_do_not_line_up(blocks):
    with pytest.raises(ValueError):
        join(blocks)


def test_order_result_counts_batches_out_of_admission_order():
    ordered = [{"id": i, "answer": {"id": i, "ok": True}} for i in range(6)]
    assert order_result(ordered, "0000")["out_of_admission_order"] == 0
    swapped = [ordered[1], ordered[0], ordered[2]] + ordered[3:]
    assert order_result(swapped, "0000")["out_of_admission_order"] == 1


def test_orderfail_decodes_which_request_of_each_pair_failed():
    bits = "0110"
    fail = {0: 1, 1: 0, 2: 0, 3: 1}  # bit 0: the second request fails; bit 1: the first fails
    r = orderfail_result(rows(8, ok=lambda i: i % 2 != fail[i // 2]), bits)
    assert r["decoded_right"] == r["decoded"] == 4 and r["erased"] == 0 and r["verdict"] == "not detected"
    none_failed = orderfail_result(rows(8), bits)
    assert none_failed["decoded_right"] == 0 and none_failed["erased"] == 4


def test_descriptive_arms_get_no_verdict_and_keep_erasures_apart():
    fail = {0: 1, 1: 0, 2: 0, 3: 1}
    # pairs 2 and 3 erased (both requests failed), pairs 0 and 1 decoded correctly
    r = orderfail_result(rows(8, ok=lambda i: i < 4 and i % 2 != fail[i // 2]), "0110", registered=False)
    assert (r["decoded"], r["decoded_right"], r["erased"]) == (2, 2, 2) and "verdict" not in r and "p" not in r


@pytest.mark.parametrize("bad", [rows(1), [rows(2)[0], rows(2)[0]], rows(3)])
def test_orderfail_refuses_incomplete_duplicate_or_extra_replies(bad):
    with pytest.raises(ValueError):
        orderfail_result(bad, "0")


@pytest.mark.parametrize("field,value", [("id", 9), ("ok", None), ("ok", 1)])
def test_orderfail_refuses_mismatched_reply_ids_or_invalid_status(field, value):
    bad = rows(2)
    bad[0]["answer"][field] = value
    with pytest.raises(ValueError):
        orderfail_result(bad, "0")


def test_verdict_follows_the_registered_rule():
    assert (verdict(0.005), verdict(0.03), verdict(0.2)) == ("leak shown", "inconclusive", "not detected")
