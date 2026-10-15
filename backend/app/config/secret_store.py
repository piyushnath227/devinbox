"""Where KeyManager keeps its (already encrypted) blobs.

* FileSecretStore -- one file per secret under KEYS_DIR. Zero setup; fine for
  a single host or several containers sharing one volume.
* DbSecretStore   -- rows in the `encrypted_secrets` table. Required once the
  app and workers run on different hosts (no shared disk), and it is covered by
  your normal database backups.

DbSecretStore transparently imports from an existing KEYS_DIR on first read, so
switching an existing install to Postgres needs no manual key migration.
"""

import base64
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger()


class SecretStore(ABC):
    @abstractmethod
    def get(self, name: str) -> Optional[bytes]: ...

    @abstractmethod
    def put(self, name: str, value: bytes) -> None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...

    @abstractmethod
    def get_or_create(self, name: str, value: bytes) -> bytes: ...


class FileSecretStore(SecretStore):
    def __init__(self, keys_dir: str):
        self.dir = Path(keys_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        # `.salt` keeps its historical name; everything else is `<name>.enc`.
        return self.dir / (name if name.startswith(".") else f"{name}.enc")

    def get(self, name: str) -> Optional[bytes]:
        p = self._path(name)
        return p.read_bytes() if p.exists() else None

    def put(self, name: str, value: bytes) -> None:
        p = self._path(name)
        import tempfile
        fd, temp = tempfile.mkstemp(dir=self.dir, prefix='.secret-')
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(value)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, p)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def get_or_create(self, name: str, value: bytes) -> bytes:
        # Hold a process-shared lock until the complete salt has reached disk.
        import fcntl
        with open(self.dir / '.init.lock', 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = self.get(name)
            if existing is None:
                self.put(name, value)
                return value
            return existing

    def delete(self, name: str) -> None:
        p = self._path(name)
        if p.exists():
            p.unlink()


class DbSecretStore(SecretStore):
    def __init__(self, fallback: Optional[FileSecretStore] = None):
        self.fallback = fallback  # legacy file store to import from (read-once)

    @staticmethod
    def _session():
        from ..models.database import create_session
        return create_session()

    def get(self, name: str) -> Optional[bytes]:
        from ..models.secrets import EncryptedSecret
        db = self._session()
        try:
            row = db.get(EncryptedSecret, name)
            if row is not None:
                return base64.b64decode(row.value)
        finally:
            db.close()
        if self.fallback is not None:
            legacy = self.fallback.get(name)
            if legacy is not None:
                self.put(name, legacy)  # import once; DB is the source of truth afterwards
                logger.info("secret_imported_from_files", name=name)
                return legacy
        return None

    def put(self, name: str, value: bytes) -> None:
        from ..models.secrets import EncryptedSecret
        encoded = base64.b64encode(value).decode("ascii")
        db = self._session()
        try:
            row = db.get(EncryptedSecret, name)
            if row is None:
                db.add(EncryptedSecret(name=name, value=encoded))
            else:
                row.value = encoded
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_or_create(self, name: str, value: bytes) -> bytes:
        from ..models.secrets import EncryptedSecret
        # Preserve the legacy salt when importing an installation.
        legacy = self.fallback.get(name) if self.fallback else None
        value = legacy if legacy is not None else value
        with self._session() as db:
            if db.bind.dialect.name == 'postgresql':
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            db.execute(insert(EncryptedSecret).values(name=name, value=base64.b64encode(value).decode())
                       .on_conflict_do_nothing(index_elements=['name']))
            db.commit()
            return base64.b64decode(db.get(EncryptedSecret, name).value)

    def delete(self, name: str) -> None:
        from ..models.secrets import EncryptedSecret
        db = self._session()
        try:
            row = db.get(EncryptedSecret, name)
            if row is not None:
                db.delete(row)
                db.commit()
        finally:
            db.close()
        if self.fallback is not None:
            self.fallback.delete(name)  # don't let a deleted key resurrect from the old file


def build_secret_store(settings) -> SecretStore:
    from ..models.database import is_sqlite
    backend = (settings.SECRETS_BACKEND or "auto").lower()
    use_db = backend == "db" or (backend == "auto" and not is_sqlite(settings.DATABASE_URL))
    files = FileSecretStore(settings.KEYS_DIR)
    return DbSecretStore(fallback=files) if use_db else files
