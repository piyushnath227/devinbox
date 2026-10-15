"""Filesystem backend: zero dependencies, ideal for local dev and contributors."""

import time
from pathlib import Path
from typing import Any, Dict

from .base import AuditArchive


class LocalAuditArchive(AuditArchive):
    name = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _put(self, key: str, body: bytes, content_type: str) -> Dict[str, Any]:
        path = (self.root / key).resolve()
        # Defence in depth: file paths in diffs come from LLM output / repo
        # contents, so never let a key escape the archive root.
        if self.root not in path.parents:
            raise ValueError(f"refusing to write outside archive root: {key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)  # atomic on POSIX
        return {"path": str(path), "bytes": len(body)}

    def health_check(self) -> Dict[str, Any]:
        try:
            start = time.time()
            probe = self.root / ".healthcheck"
            probe.write_text("ok")
            probe.unlink()
            return {"status": "healthy", "backend": self.name, "root": str(self.root),
                    "latency_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "unhealthy", "backend": self.name, "error": str(e)}
