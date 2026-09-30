"""The relay that publishes the ocr service on 127.0.0.1 (kaagaz/ocr_server/relay.py),
between two local sockets: a stand-in for ocr, and a caller."""

import asyncio

from kaagaz.ocr_server import relay


async def _through_relay(message: bytes, upstream_up: bool) -> bytes:
    async def stand_in(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"ocr:" + await reader.read(100))
        await writer.drain()
        writer.close()  # as a reply ends the connection

    ocr = await asyncio.start_server(stand_in, "127.0.0.1", 0)
    upstream = ("127.0.0.1", ocr.sockets[0].getsockname()[1])
    if not upstream_up:
        ocr.close()
        await ocr.wait_closed()
    server = await relay.start(0, upstream, host="127.0.0.1")
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        writer.write(message)
        await writer.drain()
        reply = await asyncio.wait_for(reader.read(), 5)  # to the end: the relay passes the close on
        writer.close()
        await writer.wait_closed()
    ocr.close()
    await ocr.wait_closed()
    return reply


def test_bytes_go_to_ocr_and_back() -> None:
    assert asyncio.run(_through_relay(b"POST /read", upstream_up=True)) == b"ocr:POST /read"


def test_with_ocr_down_the_caller_sees_a_closed_connection() -> None:
    assert asyncio.run(_through_relay(b"POST /read", upstream_up=False)) == b""
