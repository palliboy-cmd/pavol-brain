#!/usr/bin/env python3
"""Single bounded projector iteration guarded against overlap."""
import argparse
import fcntl
import subprocess
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lock-file", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=240)
    args, projector_args = p.parse_known_args()
    args.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_file.open("a+") as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 75
        command = [sys.executable, str(Path(__file__).with_name("run_brain_projector.py")), "--run-once", "--validate", *projector_args]
        try: return subprocess.run(command, timeout=args.timeout, check=False).returncode
        except subprocess.TimeoutExpired: return 124


if __name__ == "__main__": raise SystemExit(main())
