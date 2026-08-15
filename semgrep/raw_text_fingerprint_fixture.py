import hashlib
from pathlib import Path


def bad_restore(path: Path) -> str:
    # ruleid: correctness.raw-text-fingerprint-before-text-rewrite
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return digest


def normalized_restore(path: Path) -> str:
    # ok: correctness.raw-text-fingerprint-before-text-rewrite
    content = path.read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(content).hexdigest()
    path.write_text(content.decode("utf-8"), encoding="utf-8", newline="\n")
    return digest


def binary_restore(path: Path) -> str:
    # ok: correctness.raw-text-fingerprint-before-text-rewrite
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    path.write_bytes(content)
    return digest
