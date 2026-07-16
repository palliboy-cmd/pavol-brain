"""Deterministic server-side verification for M1 artifact URIs.

B9 / Package 6: alongside the existence verdict, ``verify`` records the
minimum a future verifier needs to re-check or upgrade a claim without a
schema migration -- ``repo_alias`` (the configured alias name, never a raw
filesystem path) and, when a stable Git object identity is cheaply
available from the same local check, ``object_digest``. Neither field is
invented: a scheme this module cannot deterministically check, or a digest
lookup that itself fails, always yields ``None`` rather than a guess.
"""
import subprocess
from pathlib import Path


def _git_digest(root, revision, request_id):
    """Best-effort resolution of a stable object id for an already-verified
    target. A failure here must never change the existence verdict already
    established by the caller -- it only leaves ``object_digest`` at None."""
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", revision],
                                capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def verify(uri, repo_roots):
    if uri.startswith("repo://"):
        value = uri[7:]
        if "/" not in value:
            return {"valid": False, "state": "verified_inactive", "reason": "malformed_uri", "method": "repo_path",
                     "repo_alias": None, "object_digest": None}
        alias, relative = value.split("/", 1)
        # B9/Package 6 (§8, scope-authorized artifact): an alias absent from
        # config.artifact_repo_roots is checked explicitly rather than
        # defaulting to "" -- Path("") is the process's cwd, which is
        # frequently itself a git checkout, and would otherwise silently
        # verify against whatever repo the server happens to be running
        # from instead of failing safe to unknown.
        if alias not in repo_roots:
            return {"valid": False, "state": "unknown", "reason": "other", "method": "repo_unavailable",
                     "repo_alias": alias, "object_digest": None}
        root = Path(repo_roots[alias])
        if not root.is_dir() or not (root / ".git").exists():
            return {"valid": False, "state": "unknown", "reason": "other", "method": "repo_unavailable",
                     "repo_alias": alias, "object_digest": None}
        result = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", relative],
                                capture_output=True, text=True, timeout=10)
        valid = result.returncode == 0
        digest = _git_digest(root, "HEAD:" + relative, uri) if valid else None
        return {"valid": valid,
                "state": "verified_active" if valid else "verified_inactive",
                "reason": "other" if valid else "wrong_target", "method": "git_ls_files",
                "repo_alias": alias, "object_digest": digest}
    if uri.startswith("git://"):
        value = uri[6:]
        marker = "/commit/"
        if marker not in value:
            return {"valid": False, "state": "verified_inactive", "reason": "malformed_uri", "method": "git_commit",
                     "repo_alias": None, "object_digest": None}
        alias, revision = value.split(marker, 1)
        if alias not in repo_roots:
            return {"valid": False, "state": "unknown", "reason": "other", "method": "repo_unavailable",
                     "repo_alias": alias, "object_digest": None}
        root = Path(repo_roots[alias])
        if not root.is_dir() or not (root / ".git").exists():
            return {"valid": False, "state": "unknown", "reason": "other", "method": "repo_unavailable",
                     "repo_alias": alias, "object_digest": None}
        result = subprocess.run(["git", "-C", str(root), "cat-file", "-e", revision + "^{commit}"],
                                capture_output=True, text=True, timeout=10)
        valid = result.returncode == 0
        digest = _git_digest(root, revision + "^{commit}", uri) if valid else None
        return {"valid": valid,
                "state": "verified_active" if valid else "verified_inactive",
                "reason": "other" if valid else "wrong_target", "method": "git_cat_file",
                "repo_alias": alias, "object_digest": digest}
    return {"valid": False, "state": "unknown", "reason": "other", "method": "not_deterministically_verifiable",
             "repo_alias": None, "object_digest": None}


def verify_all(uris, repo_roots):
    return {uri: verify(uri, repo_roots) for uri in sorted(set(uris))}
