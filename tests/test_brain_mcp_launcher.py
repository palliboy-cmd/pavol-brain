import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
LAUNCHER = ROOT / "scripts" / "run_brain_mcp_ssh.sh"
INSTALLER = ROOT / "scripts" / "install_brain_mcp_client_launcher.sh"


def make_launcher_fixture(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    copied = bin_dir / "run_brain_mcp_ssh.sh"; shutil.copy2(LAUNCHER, copied); copied.chmod(0o755)
    fake_ssh = bin_dir / "ssh"
    fake_ssh.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$FAKE_SSH_ARGS\"\nfor arg in \"$@\"; do command=\"$arg\"; done\nexec /bin/sh -c \"$command\"\n")
    fake_ssh.chmod(0o755)
    return copied, bin_dir


def run(launcher, bin_dir, **env):
    return subprocess.run([str(launcher)], text=True, capture_output=True,
                          env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], **env})


def test_copied_launcher_requires_explicit_remote_root(tmp_path):
    launcher, bin_dir = make_launcher_fixture(tmp_path)
    result = run(launcher, bin_dir, BRAIN_INTEGRATION_ID="claude")
    assert result.returncode == 64
    assert "BRAIN_MCP_REMOTE_ROOT is required" in result.stderr


def test_explicit_host_root_and_space_quoting_reach_remote_entry(tmp_path):
    launcher, bin_dir = make_launcher_fixture(tmp_path)
    remote = tmp_path / "remote root"; python = remote / ".venv/bin/python"; entry = remote / "scripts/run_brain_mcp.py"
    python.parent.mkdir(parents=True); entry.parent.mkdir(parents=True)
    marker = tmp_path / "marker"
    ssh_args = tmp_path / "ssh-args"
    python.write_text("#!/bin/sh\nprintf '%s|%s|%s|%s|%s\\n' \"$BRAIN_INSTANCE\" \"$BRAIN_INTEGRATION_ID\" \"$BRAIN_CLIENT_IDENTITY\" \"$BRAIN_JOURNAL_DB\" \"$1\" > \"$MARKER\"\n")
    python.chmod(0o755); entry.write_text("# entry\n")
    result = run(launcher, bin_dir, BRAIN_INTEGRATION_ID="claude", BRAIN_INSTANCE="personal", BRAIN_MCP_SSH_HOST="mini-explicit", BRAIN_MCP_REMOTE_ROOT=str(remote), MARKER=str(marker), FAKE_SSH_ARGS=str(ssh_args), HOME=str(tmp_path / "remote-home"))
    assert result.returncode == 0, result.stderr
    assert "mini-explicit" in ssh_args.read_text().splitlines()
    assert marker.read_text() == f"personal|claude|claude|{tmp_path}/remote-home/Library/Application Support/Pavol-Brain/personal/journal.db|{entry}\n"


def test_missing_remote_root_and_venv_are_precise(tmp_path):
    launcher, bin_dir = make_launcher_fixture(tmp_path)
    missing = run(launcher, bin_dir, BRAIN_INTEGRATION_ID="claude", BRAIN_MCP_REMOTE_ROOT=str(tmp_path / "missing"), FAKE_SSH_ARGS=str(tmp_path / "missing-args"))
    assert missing.returncode == 66 and "remote root missing" in missing.stderr
    root = tmp_path / "root"; (root / "scripts").mkdir(parents=True); (root / "scripts/run_brain_mcp.py").write_text("# entry\n")
    venv = run(launcher, bin_dir, BRAIN_INTEGRATION_ID="claude", BRAIN_MCP_REMOTE_ROOT=str(root), FAKE_SSH_ARGS=str(tmp_path / "venv-args"))
    assert venv.returncode == 66 and "remote Python missing or not executable" in venv.stderr


def test_missing_remote_entry_is_precise(tmp_path):
    launcher, bin_dir = make_launcher_fixture(tmp_path)
    root = tmp_path / "root"; python = root / ".venv/bin/python"; python.parent.mkdir(parents=True); python.write_text("#!/bin/sh\n"); python.chmod(0o755)
    result = run(launcher, bin_dir, BRAIN_INTEGRATION_ID="claude", BRAIN_MCP_REMOTE_ROOT=str(root), FAKE_SSH_ARGS=str(tmp_path / "entry-args"))
    assert result.returncode == 66 and "remote MCP entry point missing" in result.stderr


def test_installer_is_idempotent_and_backs_up_a_different_regular_launcher(tmp_path):
    destination = tmp_path / "bin/run_brain_mcp_ssh.sh"; destination.parent.mkdir()
    destination.write_text("#!/bin/sh\necho old\n"); destination.chmod(0o700)
    env = os.environ | {"BRAIN_MCP_CLIENT_LAUNCHER": str(destination)}
    first = subprocess.run([str(INSTALLER)], text=True, capture_output=True, env=env)
    assert first.returncode == 0, first.stderr
    backups = list(destination.parent.glob("run_brain_mcp_ssh.sh.backup.*"))
    assert len(backups) == 1 and backups[0].read_text() == "#!/bin/sh\necho old\n"
    assert destination.read_text() == LAUNCHER.read_text()
    second = subprocess.run([str(INSTALLER)], text=True, capture_output=True, env=env)
    assert second.returncode == 0, second.stderr
    assert list(destination.parent.glob("run_brain_mcp_ssh.sh.backup.*")) == backups


def test_installer_refuses_a_symlink_destination(tmp_path):
    destination = tmp_path / "run_brain_mcp_ssh.sh"; destination.symlink_to(tmp_path / "elsewhere")
    result = subprocess.run([str(INSTALLER)], text=True, capture_output=True,
                            env=os.environ | {"BRAIN_MCP_CLIENT_LAUNCHER": str(destination)})
    assert result.returncode == 66
    assert "refusing non-regular destination" in result.stderr
