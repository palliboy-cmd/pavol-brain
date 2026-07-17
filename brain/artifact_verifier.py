"""Deterministic server-side verification for M1 artifact URIs.

B9 / Package 6: alongside the existence verdict, ``verify`` records the
minimum a future verifier needs to re-check or upgrade a claim without a
schema migration -- ``repo_alias`` (the configured alias name, never a raw
filesystem path) and, when a stable Git object identity is cheaply
available from the same local check, ``object_digest``. Neither field is
invented: a scheme this module cannot deterministically check, or a digest
lookup that itself fails, always yields ``None`` rather than a guess.

B9 repair (F1): the URI's relative-path/revision component is client
controlled and is never passed to git without argument isolation -- every
invocation below puts a literal ``--`` between git's own options and that
token, and the token is rejected outright (before any subprocess runs) if
it is empty, NUL-containing, absolute, escapes the resolved repo root, or
begins with ``-`` and could otherwise be misread as an option. Neither
guard alone is sufficient: pre-validation prevents an option-like token
from ever forming a "false existence" verdict via git's own pathspec
matching, and the ``--`` terminator is what keeps the request read-only
and non-option-consuming even if a future caller adds another token shape
this module doesn't yet reject.
"""
import subprocess
from pathlib import Path


def _git_digest(root, revision, request_id):
    """Best-effort resolution of a stable object id for an already-verified
    target. A failure here must never change the existence verdict already
    established by the caller -- it only leaves ``object_digest`` at None.

    ``revision`` here is always server-constructed (``"HEAD:" + relative`` or
    ``revision + "^{commit}"``) from a token that has already cleared
    ``_rejected_repo_path``/``_rejected_revision``, but ``--end-of-options``
    is still applied so this call can never be re-purposed to skip that
    guard -- unlike ``ls-files``/``cat-file``, ``rev-parse --verify`` treats
    a bare ``--`` as part of the revision grammar, not an options
    terminator, so ``--end-of-options`` is the correct guard here."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "--end-of-options", revision],
            capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _looks_like_option(token):
    return token.startswith("-")


def _within_repo_root(root, relative):
    """True if ``root/relative``, once symlink-resolved and normalized,
    does not escape ``root``. Guards both ``..`` traversal and the pathlib
    gotcha where an absolute right-hand operand discards ``root`` entirely
    (``Path("/repo") / "/etc/passwd" == Path("/etc/passwd")``)."""
    try:
        base = root.resolve()
        target = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return target == base or base in target.parents


def _rejected_repo_path(root, relative):
    """True if ``relative`` must never reach a git subprocess."""
    if not relative or "\x00" in relative:
        return True
    if _looks_like_option(relative) or Path(relative).is_absolute():
        return True
    return not _within_repo_root(root, relative)


def _rejected_revision(revision):
    """True if ``revision`` must never reach a git subprocess."""
    if not revision or "\x00" in revision:
        return True
    return _looks_like_option(revision)


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
        if _rejected_repo_path(root, relative):
            return {"valid": False, "state": "verified_inactive", "reason": "malformed_uri", "method": "repo_path",
                     "repo_alias": alias, "object_digest": None}
        result = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
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
        if _rejected_revision(revision):
            return {"valid": False, "state": "verified_inactive", "reason": "malformed_uri", "method": "git_commit",
                     "repo_alias": alias, "object_digest": None}
        result = subprocess.run(["git", "-C", str(root), "cat-file", "-e", "--", revision + "^{commit}"],
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
