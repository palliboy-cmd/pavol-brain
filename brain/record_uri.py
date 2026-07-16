"""Fail-closed classification of artifact_links.artifact_uri values (Package 5 F1 repair).

Shared by Repository.related() and Brain's relation-scope filter so both
layers agree on exactly one canonical shape. Only a byte-exact
``record://<id>`` -- lowercase scheme, single id segment, no surrounding
whitespace, no query/fragment -- resolves as a record relation. Anything
that merely *looks* like a record scheme (wrong case, extra/missing
slashes, surrounding whitespace, query/fragment, or percent-encoded) is
"record-like but malformed": it must never be tolerantly resolved to a
target id, and it must never fall through and be treated as an ordinary
artifact URI either, since its raw text still names a foreign record.
"""
import re
from urllib.parse import unquote

CANONICAL_RECORD_URI_RE = re.compile(r"^record://[^/\s?#]+$")

CANONICAL_RECORD_TARGET = "canonical_record_target"
MALFORMED_RECORD_LIKE = "malformed_record_like"
NORMAL_ARTIFACT = "normal_artifact"


def classify_record_uri(uri):
    if not uri:
        return NORMAL_ARTIFACT
    if CANONICAL_RECORD_URI_RE.fullmatch(uri):
        return CANONICAL_RECORD_TARGET
    # Narrow, testable widen: catch whitespace-padded and percent-encoded
    # spellings of the same scheme prefix. Anchored at the start so an
    # unrelated URI that merely contains the substring "record" (e.g.
    # doc://records-overview) is never misclassified.
    probe = unquote(uri).strip().lower()
    if probe.startswith("record:"):
        return MALFORMED_RECORD_LIKE
    return NORMAL_ARTIFACT


def record_target_id(uri):
    return uri[len("record://"):]
