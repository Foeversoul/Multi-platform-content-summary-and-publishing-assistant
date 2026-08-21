"""Run a pure-Python Redis-compatible server for local dev.

Uses fakeredis' TcpFakeServer, which speaks the RESP protocol over TCP and
supports the stream commands the app needs (XADD/XGROUP/XREADGROUP/XACK).
Falls back to this when no native redis-server binary is available.

Patches the per-connection handler: stock fakeredis closes the TCP connection
after *any* exception, including normal Redis error replies (BUSYGROUP from
XGROUP CREATE when the group already exists). That kills the connection pool
mid-loop. A SimpleError is a legitimate client-facing error reply, so we dump
it and keep the connection alive, matching real redis-server behaviour.
"""

import logging
import time

logging.basicConfig(level=logging.DEBUG, format="FR:%(message)s")

from fakeredis._helpers import SimpleError
from fakeredis._tcp_server import TCPFakeRequestHandler, TcpFakeServer


def _patched_handle(self) -> None:
    while not self.server._shutdown_event.is_set():
        try:
            if self.shutdown_request:
                break
            if self.current_client.can_read():
                response = self.current_client.read_response()
                self.writer.dump(response)
                continue
            data = self.rfile.readline()
            if data == b"":
                time.sleep(0)
            else:
                self.current_client.get_socket().sendall(data)
        except Exception as e:  # noqa: BLE001 — mirrors fakeredis
            self.writer.dump(e)
            # A normal Redis error reply (BUSYGROUP, wrong-type, etc.) must
            # NOT close the connection; only real transport errors should.
            if not isinstance(e, SimpleError):
                break


TCPFakeRequestHandler.handle = _patched_handle  # type: ignore[method-assign]

if __name__ == "__main__":
    server = TcpFakeServer(("127.0.0.1", 6379), server_type="redis", server_version=(8, 0))
    print("fakeredis TCP server listening on 127.0.0.1:6379", flush=True)
    server.serve_forever()
