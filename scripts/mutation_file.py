"""Byte-exact mutation helper vendored from the house mutation protocol."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MutationFile:
    """Mutate an LF view, then restore and verify the exact original bytes."""

    def __init__(self, path: Path, *, encoding: str = "utf-8") -> None:
        self.path = Path(path)
        self.encoding = encoding
        self._original_bytes: bytes | None = None
        self.original_sha256: str | None = None

    def __enter__(self) -> MutationFile:
        if self._original_bytes is not None:
            raise RuntimeError(f"mutation target already entered: {self.path}")
        self._original_bytes = self.path.read_bytes()
        self.original_sha256 = sha256(self._original_bytes)
        return self

    def original_bytes(self) -> bytes:
        if self._original_bytes is None:
            raise RuntimeError(f"mutation target was not entered: {self.path}")
        return self._original_bytes

    def apply(self, anchor: str, replacement: str) -> None:
        text = self.original_bytes().replace(b"\r\n", b"\n").decode(self.encoding)
        count = text.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"mutation anchor count must be 1, got {count}: {self.path}"
            )
        mutated = text.replace(anchor, replacement, 1)
        if mutated == text:
            raise RuntimeError(f"mutation did not change text: {self.path}")
        self.path.write_text(mutated, encoding=self.encoding, newline="\n")

    def restore(self) -> None:
        original = self.original_bytes()
        self.path.write_bytes(original)
        actual = sha256(self.path.read_bytes())
        if actual != self.original_sha256:
            raise RuntimeError(
                f"byte-exact mutation restore failed: {self.path}; "
                f"expected sha256={self.original_sha256}, actual sha256={actual}"
            )

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.restore()
        return False
