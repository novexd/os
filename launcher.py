#!/usr/bin/env python3
"""HidenCloud launcher: independently supervise Prowl and the RSS relay.

Place this file alongside start.py, rss_worker.py, .env.local and last_seen.json.
Set HidenCloud's startup file to launcher.py (leave other startup settings alone).
"""
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("launcher")
STOP = False
CHILDREN = {}
# Prowl already has its own internal restart loop, but also supervise the process.
PROGRAMS = {"prowl": BASE / "start.py", "rss": BASE / "rss_worker.py"}


def handle_stop(signum, frame):
    global STOP
    STOP = True


def spawn(name, path):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # Child stdout/stderr inherit the HidenCloud console.
    child = subprocess.Popen([sys.executable, "-u", str(path)], cwd=str(BASE), env=env)
    LOG.info("%s started (PID %s)", name, child.pid)
    return child


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    missing = [str(path) for path in PROGRAMS.values() if not path.is_file()]
    if missing:
        LOG.error("Missing required files: %s", ", ".join(missing))
        return 1

    # Load .env.local before spawning both children; never print its contents.
    # Prowl already reads it itself, and rss_worker.py also reads it independently.
    next_start = {name: 0.0 for name in PROGRAMS}
    backoff = {name: 5 for name in PROGRAMS}
    started = {}
    try:
        while not STOP:
            now = time.monotonic()
            for name, path in PROGRAMS.items():
                child = CHILDREN.get(name)
                if child is not None:
                    code = child.poll()
                    if code is None:
                        continue
                    CHILDREN.pop(name, None)
                    runtime = now - started.get(name, now)
                    backoff[name] = 5 if runtime >= 600 else min(backoff[name] * 2, 300)
                    next_start[name] = now + backoff[name]
                    LOG.warning("%s exited (code %s); retrying in %ss", name, code, backoff[name])
                if now >= next_start[name]:
                    try:
                        CHILDREN[name] = spawn(name, path)
                        started[name] = time.monotonic()
                    except OSError:
                        LOG.exception("Could not start %s", name)
                        next_start[name] = time.monotonic() + backoff[name]
                        backoff[name] = min(backoff[name] * 2, 300)
            time.sleep(1)
    finally:
        LOG.info("Stopping child processes")
        for child in CHILDREN.values():
            if child.poll() is None:
                child.terminate()
        for child in CHILDREN.values():
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
