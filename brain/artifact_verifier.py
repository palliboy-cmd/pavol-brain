"""Deterministic server-side verification for M1 artifact URIs."""
import subprocess
from pathlib import Path


def verify(uri, repo_roots):
    if uri.startswith("repo://"):
        value = uri[7:]
        if "/" not in value:
            return {"valid": False, "state": "verified_inactive", "reason": "malformed_uri", "method": "repo_path"}
        alias, relative = value.split("/", 1)
        root = Path(repo_roots.get(alias, ""))
        if not root.is_dir() or not (root / ".git").exists():
            return {"valid": False, "state": "unknown", "reason": "other", "method": "repo_unavailable"}
        result = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", relative],
                                capture_output=True, text=True, timeout=10)
        return {"valid": result.returncode == 0,
                "state": "verified_active" if result.returncode == 0 else "verified_inactive",
                "reason": "other" if result.returncode == 0 else "wrong_target", "method": "git_ls_files"}
    if uri.startswith("git://"):
        value = uri[6:]
        marker = "/commit/"
        if marker not in value:
            return {"valid": False, "state": "verified_inactive", "reason": "malformed_uri", "method": "git_commit"}
        alias, revision = value.split(marker, 1)
        root = Path(repo_roots.get(alias, ""))
        if not root.is_dir() or not (root / ".git").exists():
            return {"valid": False, "state": "unknown", "reason": "other", "method": "repo_unavailable"}
        result = subprocess.run(["git", "-C", str(root), "cat-file", "-e", revision + "^{commit}"],
                                capture_output=True, text=True, timeout=10)
        return {"valid": result.returncode == 0,
                "state": "verified_active" if result.returncode == 0 else "verified_inactive",
                "reason": "other" if result.returncode == 0 else "wrong_target", "method": "git_cat_file"}
    return {"valid": False, "state": "unknown", "reason": "other", "method": "not_deterministically_verifiable"}


def verify_all(uris, repo_roots):
    return {uri: verify(uri, repo_roots) for uri in sorted(set(uris))}
