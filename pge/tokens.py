"""Token-id messages for the FlashProve backend, as endpoint/wire/src/tokens.rs: kinds 5 to 8, a
big-endian id, then big-endian u32 token ids; an answer ends with the proof; a reply carries a status
byte and NC ids, or zeros for an error. See docs/FLASHPROVE_BINDING.md.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from pge.protocol import MAX_ID, ProtocolError

REQUEST, FORWARD, ANSWER, REPLY = 5, 6, 7, 8
OK, BAD_REQUEST, UNAVAILABLE = 0, 1, 2
PROOF_LIMIT = 1 << 26
_HEAD = struct.Struct(">BQ")


def read_pins(text: str) -> dict:
    """The settings of a FlashProve pins file (device/flashprove_small.pins), without its file lines."""
    out = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key != "file":
            out[key] = value
    return out


@dataclass(frozen=True)
class TokenWire:
    prompt: int
    reply: int
    proof_max: int

    def __post_init__(self):
        if self.prompt < 1 or self.reply < 1 or not 1 <= self.proof_max <= PROOF_LIMIT:
            raise ValueError("token wire sizes out of range")

    @classmethod
    def from_pins(cls, pins: dict) -> "TokenWire":
        return cls(int(pins["prompt"]), int(pins["reply"]), int(pins["proof-bytes"]))

    @property
    def request_size(self) -> int:
        return _HEAD.size + 4 * self.prompt

    @property
    def answer_fixed(self) -> int:
        return _HEAD.size + 4 * self.reply

    @property
    def answer_max(self) -> int:
        return self.answer_fixed + self.proof_max

    @property
    def reply_size(self) -> int:
        return _HEAD.size + 1 + 4 * self.reply

    @staticmethod
    def _ids(ids, n: int, what: str) -> bytes:
        ids = list(ids)
        if len(ids) != n or any(type(t) is not int or not 0 <= t < 1 << 32 for t in ids):
            raise ProtocolError(f"{what} needs {n} token ids")
        return struct.pack(f">{n}I", *ids)

    @staticmethod
    def _head(kind: int, i: int) -> bytes:
        if type(i) is not int or not 0 <= i < MAX_ID:
            raise ProtocolError("id out of range")
        return _HEAD.pack(kind, i)

    def _parse(self, data: bytes, kind: int, size: int | None, what: str) -> int:
        if size is not None and len(data) != size:
            raise ProtocolError(f"{what} has {len(data)} bytes, expected {size}")
        if len(data) < _HEAD.size or data[0] != kind:
            raise ProtocolError(f"not a {what}")
        i = _HEAD.unpack_from(data)[1]
        if i >= MAX_ID:
            raise ProtocolError("id out of range")
        return i

    def encode_request(self, cid: int, ids) -> bytes:
        return self._head(REQUEST, cid) + self._ids(ids, self.prompt, "request")

    def parse_request(self, data: bytes) -> tuple[int, list[int]]:
        cid = self._parse(data, REQUEST, self.request_size, "request")
        return cid, list(struct.unpack_from(f">{self.prompt}I", data, _HEAD.size))

    def encode_forward(self, seq: int, ids) -> bytes:
        return self._head(FORWARD, seq) + self._ids(ids, self.prompt, "forward")

    def parse_forward(self, data: bytes) -> tuple[int, list[int]]:
        seq = self._parse(data, FORWARD, self.request_size, "forward")
        return seq, list(struct.unpack_from(f">{self.prompt}I", data, _HEAD.size))

    def encode_answer(self, seq: int, ids, proof: bytes) -> bytes:
        if not 1 <= len(proof) <= self.proof_max:
            raise ProtocolError("proof length out of range")
        return self._head(ANSWER, seq) + self._ids(ids, self.reply, "answer") + bytes(proof)

    def parse_answer(self, data: bytes) -> tuple[int, list[int], bytes]:
        if not self.answer_fixed < len(data) <= self.answer_max:
            raise ProtocolError("answer length out of range")
        seq = self._parse(data, ANSWER, None, "answer")
        return seq, list(struct.unpack_from(f">{self.reply}I", data, _HEAD.size)), data[self.answer_fixed:]

    def reply_ok(self, cid: int, ids) -> bytes:
        return self._head(REPLY, cid) + bytes([OK]) + self._ids(ids, self.reply, "reply")

    def reply_error(self, cid: int, error: str) -> bytes:
        code = {"bad_request": BAD_REQUEST, "unavailable": UNAVAILABLE}[error]
        return self._head(REPLY, cid) + bytes([code]) + bytes(4 * self.reply)

    def parse_reply(self, data: bytes) -> dict:
        cid = self._parse(data, REPLY, self.reply_size, "reply")
        status, body = data[_HEAD.size], data[_HEAD.size + 1:]
        if status == OK:
            return {"id": cid, "ok": True, "y": list(struct.unpack(f">{self.reply}I", body))}
        for name, code in (("bad_request", BAD_REQUEST), ("unavailable", UNAVAILABLE)):
            if status == code and not any(body):
                return {"id": cid, "ok": False, "error": name}
        raise ProtocolError("reply has an unknown status or a non-zero error body")
