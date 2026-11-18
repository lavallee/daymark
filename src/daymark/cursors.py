"""Portable checkpoints for the boundary event of a workspace activity page."""

import codecs
import hashlib
import json
import re

from .models import DomainError

_STAMP = re.compile(r"dm1\.(0|[1-9][0-9]{0,18})\.[0-9a-f]{32}")
MAX_CURSOR = 2**63 - 1


def numeric_or_checkpoint(value):
    """HTTP and CLI receive strings even when a caller supplied a numeric cursor."""
    if value is None or value.startswith("dm1."):
        return value
    try:
        return int(value)
    except ValueError:
        return value


def cursor_number(value):
    if isinstance(value, str) and (match := _STAMP.fullmatch(value)):
        value = int(match[1])
    if type(value) is not int or not 0 <= value <= MAX_CURSOR:
        raise DomainError(
            "invalid", "Activity cursor must be a nonnegative integer or a saved checkpoint"
        )
    return value


def mismatch():
    return DomainError(
        "conflict",
        "Activity checkpoint does not match this workspace history",
        ["Run prime --brief again and resume from its current checkpoint."],
    )


def checkpoint(store, workspace, seq):
    return f"dm1.{seq}.{event_fingerprint(store, workspace, seq)}"


def event_fingerprint(store, workspace, seq):
    """Hash one event in the caller's snapshot, independent of physical SQLite row IDs."""
    digest = hashlib.blake2b(digest_size=16)
    metadata = [workspace, seq]
    if seq:
        row = store.db.execute(
            "SELECT e.seq,e.operation_id,e.entity_id,e.action,e.created_at,e.actor,"
            "e.before_data IS NOT NULL,e.after_data IS NOT NULL FROM daymark_event_cursors c "
            "JOIN daymark_events e ON e.seq=c.event_seq WHERE c.workspace=? AND c.seq=?",
            (workspace, seq),
        ).fetchone()
        if not row:
            raise mismatch()
        metadata.extend(row[1:6])
    digest.update(json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode())
    if seq:
        encoding = store._text_encoding()
        for field, present in zip(("before_data", "after_data"), row[6:], strict=True):
            digest.update(bytes([present]))
            if present:
                with store.db.blobopen("daymark_events", field, row[0], readonly=True) as blob:
                    field_hash = hashlib.blake2b(digest_size=16)
                    decoder = (
                        codecs.getincrementaldecoder(encoding)() if encoding != "utf-8" else None
                    )
                    while chunk := blob.read(32768):
                        field_hash.update(decoder.decode(chunk).encode() if decoder else chunk)
                    if decoder:
                        field_hash.update(decoder.decode(b"", final=True).encode())
                    digest.update(field_hash.digest())
    return digest.hexdigest()
