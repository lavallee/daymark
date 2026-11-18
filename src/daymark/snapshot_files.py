"""Publish complete local backups and workspace exports without replacing another file."""

import errno
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path


@contextmanager
def _new_snapshot(destination, prefix, fallback):
    if os.path.lexists(destination):
        raise FileExistsError("Destination already exists")
    fd, name = tempfile.mkstemp(prefix=prefix, dir=destination.parent)
    os.close(fd)
    prepared = Path(name)
    try:
        yield prepared
        if os.name == "nt":
            os.rename(prepared, destination)  # Windows rename refuses an existing name.
            return
        try:
            os.link(prepared, destination)
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.ENOSYS):
                raise
            # Some removable filesystems cannot link two names to one file.
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            reserved = os.fstat(fd)
            os.close(fd)
            try:
                fallback(prepared, destination)
            except BaseException:
                if destination.exists() and os.path.samestat(reserved, destination.stat()):
                    destination.unlink()
                raise
    finally:
        prepared.unlink(missing_ok=True)


def _copy_sqlite(source, destination):
    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader,
        closing(sqlite3.connect(destination)) as writer,
    ):
        reader.backup(writer)
        # A sealed backup can be read on a volume with no writable sidecar directory.
        writer.execute("PRAGMA journal_mode=DELETE")
        if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")


def copy_database(source: Path, destination: Path):
    """Copy a committed SQLite snapshot, including the contents of its WAL."""
    if not source.is_file():
        raise ValueError("Source database missing")
    if source.stat().st_size == 0:
        raise ValueError("Source is an empty file, not a SQLite backup")
    with _new_snapshot(destination, ".daymark-backup-", _copy_sqlite) as prepared:
        _copy_sqlite(source, prepared)


def write_export(destination: Path, snapshot):
    """Serialize first so an interruption cannot publish a partial JSON document."""
    with (
        _new_snapshot(destination, ".daymark-export-", shutil.copyfile) as prepared,
        prepared.open("w", encoding="utf-8") as stream,
    ):
        json.dump(snapshot, stream, default=str, sort_keys=True)
