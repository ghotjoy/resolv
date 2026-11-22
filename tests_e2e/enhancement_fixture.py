from contextlib import contextmanager
import os
from pathlib import Path
import socket
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import URLError
from urllib.request import urlopen

DEMO_PASSWORD = secrets.token_urlsafe(18)

PROJECT = Path(__file__).resolve().parents[1]


@contextmanager
def running_server(**settings):
    with TemporaryDirectory() as tmp, socket.socket() as port_check:
        port_check.bind(("127.0.0.1", 0))
        port = port_check.getsockname()[1]
        port_check.close()
        path = Path(tmp)
        env = {**os.environ, "RESOLV_HOST":"127.0.0.1", "RESOLV_PORT":str(port),
               "RESOLV_DATABASE":str(path / "test.db"), "RESOLV_ATTACHMENTS":str(path / "evidence"),
               "RESOLV_SMTP_HOST":"", "RESOLV_ADMIN_PASSWORD":"", "RESOLV_DEMO_PASSWORD":DEMO_PASSWORD, **settings}
        env.pop("RESOLV_ADMIN_PASSWORD", None)
        url = f"http://127.0.0.1:{port}"
        with subprocess.Popen([sys.executable, "server.py"], cwd=PROJECT, env=env) as server:
            try:
                wait_for_server(url, server)
                yield url, path
            finally:
                server.terminate()
                server.wait(timeout=5)


def wait_for_server(url, server):
    for _ in range(150):
        if server.poll() is not None:
            raise RuntimeError("Resolv test server stopped during startup")
        try:
            with urlopen(url, timeout=.3):
                return
        except URLError:
            time.sleep(.05)
    raise RuntimeError("Resolv test server timed out")
