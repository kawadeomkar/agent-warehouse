"""A vendor's download server, for the signed downloads' tests: fake_downloads.py <port> <root> serves <root>'s files
over HTTP on 127.0.0.1, 0 for a port it picks and prints, and appends each path asked for to <root>/requests, so a test
can say what a run fetched - and what it never did.

Plain HTTP, as today's roles' tests reach it (tests/unit/test_signed_downloads.py). Given --tls with a certificate's
file and its key's after <root>, it serves HTTPS with that certificate alone, as the new engine's one transport, which
takes an https:// address only, reaches it (tests/unit/test_aw_downloads.py)."""

import functools
import http.server
import os
import ssl
import sys


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's name
        with open(os.path.join(ROOT, "requests"), "a", encoding="utf-8") as log:
            log.write(self.path + "\n")
        super().do_GET()

    def log_message(self, format, *args):  # noqa: A002 - http.server's signature
        pass


ROOT = os.path.abspath(sys.argv[2])
TLS = sys.argv[4:6] if sys.argv[3:4] == ["--tls"] else None
server = http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), functools.partial(Handler, directory=ROOT))
if TLS is not None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(TLS[0], TLS[1])
    server.socket = context.wrap_socket(server.socket, server_side=True)
print(server.server_address[1], flush=True)
server.serve_forever()
