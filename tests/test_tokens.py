import shutil
import struct

import pytest

from conftest import ROOT
from pge.protocol import ProtocolError
from pge.tokens import TokenWire, read_pins
from prover.server import Replay, Server

SMALL = ROOT / "endpoint/flashprove/tests/data/small"
PINS = ROOT / "device/flashprove_small.pins"

# The same bytes are checked by endpoint/wire/tests/tokens.rs, so both sides agree on the format.
VECTORS = {
    "request": "05" "0000000000000007" "00000001" "01020304",
    "answer": "07" "0000000000000007" "000001ff" "abcd",
    "reply_ok": "08" "0000000000000007" "00" "000001ff",
    "reply_unavailable": "08" "0000000000000007" "02" "00000000",
    "reply_bad_request": "08" "0000000000000007" "01" "00000000",
}


def test_shared_vectors():
    w = TokenWire(2, 1, 16)
    assert w.encode_request(7, [1, 0x01020304]).hex() == VECTORS["request"]
    assert w.encode_answer(7, [511], b"\xab\xcd").hex() == VECTORS["answer"]
    assert w.reply_ok(7, [511]).hex() == VECTORS["reply_ok"]
    assert w.reply_error(7, "unavailable").hex() == VECTORS["reply_unavailable"]
    assert w.reply_error(7, "bad_request").hex() == VECTORS["reply_bad_request"]
    assert (w.request_size, w.answer_fixed, w.answer_max, w.reply_size) == (17, 13, 29, 14)


def test_round_trips():
    w = TokenWire(3, 2, 8)
    assert w.parse_request(w.encode_request(5, [0, 1, 2])) == (5, [0, 1, 2])
    assert w.parse_forward(w.encode_forward(6, [3, 4, 5])) == (6, [3, 4, 5])
    assert w.parse_answer(w.encode_answer(9, [7, 8], b"proof")) == (9, [7, 8], b"proof")
    assert w.parse_reply(w.reply_ok(1, [7, 8])) == {"id": 1, "ok": True, "y": [7, 8]}
    assert w.parse_reply(w.reply_error(1, "unavailable")) == {"id": 1, "ok": False, "error": "unavailable"}


@pytest.mark.parametrize("make", [
    lambda w: w.encode_request(1, [1, 2]),
    lambda w: w.encode_request(1, [1, 2, 3, 4]),
    lambda w: w.encode_request(1, [1, 2, -1]),
    lambda w: w.encode_request(1, [1, 2, 1 << 32]),
    lambda w: w.encode_request(1 << 53, [1, 2, 3]),
    lambda w: w.encode_answer(1, [1, 2], b""),
    lambda w: w.encode_answer(1, [1, 2], b"x" * 9),
    lambda w: w.parse_request(b"\x01" + bytes(20)),
    lambda w: w.parse_request(w.encode_forward(1, [1, 2, 3])),
    lambda w: w.parse_answer(w.encode_answer(1, [1, 2], b"x")[:-1]),
    lambda w: w.parse_reply(b"\x08" + bytes(8) + b"\x02" + b"\x00\x00\x00\x01" + bytes(4)),
    lambda w: w.parse_reply(b"\x08" + bytes(8) + b"\x03" + bytes(8)),
])
def test_malformed_messages_are_refused(make):
    with pytest.raises(ProtocolError):
        make(TokenWire(3, 2, 8))


def test_pins_give_the_wire():
    w = TokenWire.from_pins(read_pins(PINS.read_text()))
    assert (w.prompt, w.reply, w.proof_max) == (61, 4, 8 << 20)


@pytest.fixture
def fixture(tmp_path):
    for name in ("example.req", "example.y"):
        shutil.copy(SMALL / name, tmp_path / name)
    (tmp_path / "example.proof").write_bytes(b"stand-in proof")
    return tmp_path


def test_replay_splits_the_stored_request(fixture):
    words = struct.unpack("<66I", (SMALL / "example.req").read_bytes())
    r = Replay(fixture, PINS)
    assert r.prompt == list(words[:61])
    assert r.reply == list(words[61:64]) + [199]
    assert r.prove(r.prompt) == (r.reply, b"stand-in proof")
    assert r.tamper(r.reply)[1:] == r.reply[1:] and r.tamper(r.reply)[0] != r.reply[0]
    assert r.tamper(r.reply, last=True) == r.reply[:3] + [200]


def test_replay_refuses_a_fixture_that_does_not_match_the_pins(fixture, tmp_path):
    other = tmp_path / "other.pins"
    other.write_text(PINS.read_text().replace("prompt 61", "prompt 60"))
    with pytest.raises(ValueError):
        Replay(fixture, other)


def test_server_uses_the_token_wire(fixture):
    s = Server(Replay(fixture, PINS), mode="flip_proof")
    assert isinstance(s.wire, TokenWire) and s.wire.request_size == 9 + 4 * 61
