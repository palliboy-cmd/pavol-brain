import fcntl
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_lock_prevents_overlap(tmp_path):
    lock_path = tmp_path / "projector.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = subprocess.run([sys.executable, str(ROOT/"scripts/run_brain_projector_locked.py"), "--lock-file", str(lock_path)])
    assert run.returncode == 75


def test_missing_required_projector_arguments_fail_nonzero(tmp_path):
    run = subprocess.run([sys.executable, str(ROOT/"scripts/run_brain_projector_locked.py"), "--lock-file", str(tmp_path/"lock")])
    assert run.returncode != 0
