"""A vendor's download server, for the signed downloads' tests: fake_downloads.py <port> <root> serves <root>'s files
over HTTP on 127.0.0.1, 0 for a port it picks and prints, and appends each path asked for to <root>/requests, so a test
can say what a run fetched - and what it never did."""

import functools
import http.server
import os
import sys


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's name
        with open(os.path.join(ROOT, "requests"), "a", encoding="utf-8") as log:
            log.write(self.path + "\n")
        super().do_GET()

    def log_message(self, format, *args):  # noqa: A002 - http.server's signature
        pass


ROOT = os.path.abspath(sys.argv[2])
server = http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), functools.partial(Handler, directory=ROOT))
print(server.server_address[1], flush=True)
server.serve_forever()
