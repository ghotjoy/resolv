"""Start an isolated local Resolv demo and open it in your browser."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent


def demo_password():
    path = ROOT / ".resolv-local-password"
    try:
        with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as handle:
            handle.write(secrets.token_urlsafe(18) + "\n")
    except FileExistsError:
        pass
    return path.read_text().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reset-demo-password", action="store_true",
                        help="reset the six original demo accounts to the launcher password")
    options = parser.parse_args()
    if not 1024 <= options.port <= 65535:
        parser.error("port must be from 1024 to 65535")

    password = demo_password()
    settings = {key: value for key, value in os.environ.items() if not key.startswith("RESOLV_")}
    settings.update(RESOLV_HOST="127.0.0.1", RESOLV_PORT=str(options.port),
                    RESOLV_DEMO_PASSWORD=password, RESOLV_DEMO_LAUNCHER="1",
                    RESOLV_RESET_DEMO_PASSWORD="1" if options.reset_demo_password else "0", RESOLV_DATABASE=str(ROOT / ".resolv-local.db"),
                    RESOLV_ATTACHMENTS=str(ROOT / ".resolv-local-evidence"), RESOLV_MONITOR_SECONDS="30",
                    PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    url = f"http://127.0.0.1:{options.port}/"
    with subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT, env=settings) as app:
        try:
            for _ in range(100):
                if app.poll() is not None:
                    raise SystemExit("Resolv could not start. See the startup message above.")
                try:
                    with urlopen(url + "api/public-config", timeout=.5):
                        pass
                    if app.poll() is None:
                        break
                except URLError:
                    time.sleep(.1)
            else:
                raise SystemExit("Resolv did not respond during startup.")
            print(f"Open {url}", flush=True)
            print("Use A1 for admin, E1 for employee or T1 for technician.", flush=True)
            print(f"Demo password: {password}", flush=True)
            print("Keep this terminal open. Press Ctrl+C to stop the demo.", flush=True)
            if not options.no_browser:
                webbrowser.open(url)
            return app.wait()
        except KeyboardInterrupt:
            return 0
        finally:
            if app.poll() is None:
                app.terminate()
                app.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
