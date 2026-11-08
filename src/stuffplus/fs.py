"""Filesystem helpers shared by the data layers."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """Yield a temp path beside `path`; on success, rename it over `path` in one step.

    Readers see either the old file or the complete new one, never a partial write.
    If the body raises, `path` is untouched and the temp file is removed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        yield tmp
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
