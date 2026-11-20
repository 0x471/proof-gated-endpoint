"""The ordering experiment's positive control, not an endpoint.

It forwards each client request to the server and passes the server's answers back the moment they arrive:
no proof check, no deadline, the server's order. Rust's `--release arrival` still releases in admission order,
so it can't show that an order encoder works; this relay can. Token wire only (docs/FLASHPROVE_BINDING.md).
A harness for measurements: never put it between real parties.

Usage: python observer/relay.py --listen HOST:PORT --server HOST:PORT --pins device/flashprove_small.pins
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pge.protocol import ProtocolError, frame, read_frame  # noqa: E402
from pge.tokens import TokenWire, read_pins  # noqa: E402


class Relay:
    def __init__(self, wire: TokenWire):
        self.wire = wire
        self.next_seq = 1
        self.waiting: dict[int, tuple[asyncio.StreamWriter, int]] = {}
        self.server: asyncio.StreamWriter | None = None

    async def connect(self, host: str, port: int) -> None:
        reader, self.server = await asyncio.open_connection(host, port)
        asyncio.create_task(self._answers(reader))

    async def _answers(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                data = await read_frame(reader, self.wire.answer_max)
            except ProtocolError:
                return  # a broken frame leaves the stream out of step: stop, as the endpoint would
            if data is None:
                return
            try:
                seq, ids, _proof = self.wire.parse_answer(data)
            except ProtocolError:
                continue
            client = self.waiting.pop(seq, None)
            if client is None:
                continue
            writer, cid = client
            try:
                writer.write(frame(self.wire.reply_ok(cid, ids)))
                await writer.drain()
            except ConnectionError:
                pass

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await read_frame(reader, self.wire.request_size)
                if data is None or self.server is None:
                    break
                cid, ids = self.wire.parse_request(data)
                seq, self.next_seq = self.next_seq, self.next_seq + 1
                self.waiting[seq] = (writer, cid)
                self.server.write(frame(self.wire.encode_forward(seq, ids)))
                await self.server.drain()
        except (ProtocolError, ConnectionError):
            pass
        finally:
            writer.close()


async def start(listen: tuple[str, int], server: tuple[str, int], wire: TokenWire) -> asyncio.Server:
    relay = Relay(wire)
    await relay.connect(*server)
    return await asyncio.start_server(relay.client, *listen)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--pins", required=True, help="the endpoint's FlashProve pins file (message sizes)")
    a = ap.parse_args(argv)
    wire = TokenWire.from_pins(read_pins(Path(a.pins).read_text()))
    addr = lambda s: (s.rpartition(":")[0], int(s.rpartition(":")[2]))

    async def run() -> None:
        srv = await start(addr(a.listen), addr(a.server), wire)
        print(f"relay listening on {a.listen}, forwarding to {a.server} (no checks)", file=sys.stderr)
        await srv.serve_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()
