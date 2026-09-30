"""The ocr service on this laptop's 127.0.0.1:8080, for a worker run outside
Docker (``docker-compose.ocr-host.yml``, PRD 2 P1).

An internal network cannot publish a port, and the ocr container on a network
that can would have a way out to the internet (security A4). So ocr stays on
its internal network only, and this relay, in its own container on that network
and one that can publish, passes bytes to ``ocr:8080`` and back. It reads no
request, logs nothing and connects nowhere else; the server still checks every
request itself. Run as ``python -m kaagaz.ocr_server.relay``, standard library only.

Time and memory: O(bytes passed), 64 KiB at a time per direction.
"""

import asyncio
import contextlib

UPSTREAM = ("ocr", 8080)
PORT = 8080
_CHUNK = 64 * 1024


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Bytes from reader to writer until the reader ends, then end the writer's side."""
    with contextlib.suppress(OSError):  # either end went away: the connection is over
        while chunk := await reader.read(_CHUNK):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()


async def _relay(
    client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream: tuple[str, int]
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(*upstream)
    except OSError:  # ocr is down or still starting: the caller sees a closed connection
        client_writer.close()
        return
    await asyncio.gather(_pipe(client_reader, upstream_writer), _pipe(upstream_reader, client_writer))
    upstream_writer.close()
    client_writer.close()


async def start(port: int, upstream: tuple[str, int], host: str | None = None) -> asyncio.Server:
    """The relay listening on ``port``; ``host`` None is every interface of its own
    container, which compose publishes on 127.0.0.1 only."""
    return await asyncio.start_server(lambda r, w: _relay(r, w, upstream), host, port)


async def _serve() -> None:
    server = await start(PORT, UPSTREAM)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_serve())
