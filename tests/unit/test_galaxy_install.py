"""just install's ansible-galaxy against a stand-in Galaxy on loopback: one failed request is retried, not fatal.

ansible-galaxy's response cache (ansible-core 2.21) writes an empty entry for a request before it makes it, and when
the request fails with an error it retries - a 502, a timeout - the retry finds that entry and stops with "Missing
expected 'results' in ansible-galaxy cache", never asking Galaxy again: one bad answer from Galaxy failed a first
`just init`. `just install` therefore runs it with --no-cache (test_justfile.py holds the recipe's arguments), and this
runs it so against a Galaxy that fails the first listing of versions with a 502, then serves a collection built here.
Nothing reaches the real Galaxy, and nothing is written under the developer's ~/.ansible.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harness import _env

GALAXY = Path(sys.executable).with_name("ansible-galaxy")
VERSIONS = "/api/v3/collections/acme/demo/versions/?limit=100"


def _collection(root: Path) -> Path:
    """acme.demo 1.0.0, built as Galaxy serves a collection."""
    source = root / "source"
    source.mkdir()
    (source / "README.md").write_text("A collection for the stand-in Galaxy.\n")
    galaxy_yml = "namespace: acme\nname: demo\nversion: 1.0.0\nreadme: README.md\nauthors: [test]\n"
    (source / "galaxy.yml").write_text(galaxy_yml)
    built = root / "built"
    result = subprocess.run(
        [str(GALAXY), "collection", "build", str(source), "--output-path", str(built)],
        env=_env(root, HOME=str(root / "home")),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return built / "acme-demo-1.0.0.tar.gz"


def _galaxy(artifact: Path, failures: int) -> tuple[ThreadingHTTPServer, list[str]]:
    """A Galaxy that answers the v3 API for acme.demo, failing its first `failures` listings of versions with a 502."""
    asked: list[str] = []
    left = [failures]
    body = artifact.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _send(self, code: int, data: bytes, kind: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            asked.append(self.path)
            here = f"http://127.0.0.1:{self.server.server_port}"
            answers = {
                "/api/": {"available_versions": {"v3": "v3/"}},
                # What the cache, when on, asks first: whether the collection changed since it was cached.
                "/api/v3/collections/acme/demo/": {
                    "href": "/api/v3/collections/acme/demo/",
                    "namespace": "acme",
                    "name": "demo",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "highest_version": {"version": "1.0.0"},
                },
                VERSIONS: {
                    "meta": {"count": 1},
                    "links": {"next": None},
                    "data": [{"version": "1.0.0", "href": "/api/v3/collections/acme/demo/versions/1.0.0/"}],
                },
                "/api/v3/collections/acme/demo/versions/1.0.0/": {
                    "href": "/api/v3/collections/acme/demo/versions/1.0.0/",
                    "namespace": {"name": "acme"},
                    "collection": {"name": "demo"},
                    "version": "1.0.0",
                    "download_url": f"{here}/download/{artifact.name}",
                    "artifact": {"sha256": hashlib.sha256(body).hexdigest()},
                    "metadata": {"dependencies": {}},
                    "signatures": [],
                },
            }
            if self.path == VERSIONS and left[0]:
                left[0] -= 1
                self._send(502, b'{"detail": "Bad Gateway"}')
            elif self.path in answers:
                self._send(200, json.dumps(answers[self.path]).encode())
            elif self.path == f"/download/{artifact.name}":
                self._send(200, body, "application/gzip")
            else:
                self._send(404, b'{"detail": "Not found."}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, asked


def test_one_failed_request_to_galaxy_is_retried_and_the_collection_installed(tmp_path: Path) -> None:
    server, asked = _galaxy(_collection(tmp_path), failures=1)
    collections = tmp_path / "collections"
    requirements = tmp_path / "requirements.yml"
    requirements.write_text("collections:\n  - name: acme.demo\n    version: 1.0.0\n")
    try:
        result = subprocess.run(
            # As just install runs it, against the stand-in.
            [str(GALAXY), "collection", "install", "-r", str(requirements), "-p", str(collections), "--no-cache"]
            + ["-s", f"http://127.0.0.1:{server.server_port}/api/"],
            env=_env(
                tmp_path,
                HOME=str(tmp_path / "home"),
                ANSIBLE_GALAXY_CACHE_DIR=str(tmp_path / "galaxy_cache"),
                ANSIBLE_COLLECTIONS_PATH=str(collections),
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Missing expected 'results'" not in output
    # The 502, then the retry, which reached Galaxy.
    assert asked.count(VERSIONS) == 2, asked
    manifest = json.loads((collections / "ansible_collections" / "acme" / "demo" / "MANIFEST.json").read_text())
    assert manifest["collection_info"]["version"] == "1.0.0"
