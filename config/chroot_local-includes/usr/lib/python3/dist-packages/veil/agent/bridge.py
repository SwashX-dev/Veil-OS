"""Minimal Unix-socket -> TCP bridge, run INSIDE the inference sandbox.

Usage: python3 -I -m veil.agent.bridge SOCKET_PATH HOST PORT

It lets the agent reach a TCP-only server (e.g. Ollama) that lives in an
empty network namespace, without giving that namespace any connectivity.
"""

import asyncio
import os
import sys


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def main(path: str, host: str, port: int) -> None:
    async def handle(r, w):
        for _ in range(50):
            try:
                tr, tw = await asyncio.open_connection(host, port)
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            w.close()
            return
        await asyncio.gather(_pipe(r, tw), _pipe(tr, w))

    if os.path.exists(path):
        os.unlink(path)
    server = await asyncio.start_unix_server(handle, path=path)
    os.chmod(path, 0o600)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
