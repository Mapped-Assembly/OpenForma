"""Local product persistence with immutable snapshots and optimistic concurrency."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .base import canonical_json, fingerprint
from .composition import ProductBundle


def load_product(path: str | Path) -> ProductBundle:
    """Load a portable product, retaining independently invalid drafts for diagnosis."""
    return ProductBundle.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_product(bundle: ProductBundle, path: str | Path, *, expected_sha256: str | None = None) -> str:
    """Atomically save and snapshot a product; require a digest to replace an existing file.

    The exclusive lock prevents concurrent local writers from passing the same
    compare-and-swap check. Interrupted writes never replace the last good file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Product is locked by another writer; inspect the lock before retrying.") from exc
    temporary: str | None = None
    try:
        os.close(descriptor)
        current = fingerprint(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None
        if current != expected_sha256:
            raise ValueError("Product revision changed; reload before saving.")
        content = canonical_json(bundle)
        digest = fingerprint(bundle)
        history = path.with_name(path.name + ".revisions")
        history.mkdir(exist_ok=True)
        snapshot = history / f"{digest}.json"
        if snapshot.exists() and snapshot.read_text(encoding="utf-8") != content + "\n":
            raise ValueError("Existing revision snapshot is corrupt; restore it before saving.")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(content + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Publish a complete immutable snapshot; no partial file is visible if
        # the process is interrupted while writing the temporary file.
        if not snapshot.exists():
            os.link(temporary, snapshot)
        os.replace(temporary, path)
        temporary = None
        return digest
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
