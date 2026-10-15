"""Encrypted storage and retrieval of API keys for the LLM provider, GitHub and audit storage.

Keys are encrypted at rest with Fernet, using a key derived from SECRET_KEY
via PBKDF2, and are never logged.
"""

import json
import os
import base64
from pathlib import Path
from typing import Optional, Dict
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import structlog

from .secret_store import SecretStore, FileSecretStore

logger = structlog.get_logger()


class SecretReadError(RuntimeError):
    """A stored secret could not be read; never treat this as first-run setup."""



class KeyManager:
    """Manages encrypted storage and retrieval of API keys."""

    SALT_FILE_NAME = ".salt"
    SALT_BYTES = 16

    def __init__(self, keys_dir: str, secret_key: str, store: Optional[SecretStore] = None):
        self.keys_dir = Path(keys_dir)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self._store = store or FileSecretStore(keys_dir)
        self._fernet = self._create_fernet(secret_key)
        logger.info("key_manager_initialized", store=type(self._store).__name__)

    def _get_or_create_salt(self) -> bytes:
        """Load this installation's PBKDF2 salt, generating and persisting a
        random one on first run. A per-installation random salt (rather than
        one hardcoded in source) is required for PBKDF2 to provide its
        intended protection."""
        return self._store.get_or_create(self.SALT_FILE_NAME, os.urandom(self.SALT_BYTES))

    def _create_fernet(self, secret_key: str) -> Fernet:
        key_bytes = secret_key.encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self._get_or_create_salt(),
            iterations=480000,
        )
        derived_key = base64.urlsafe_b64encode(kdf.derive(key_bytes))
        return Fernet(derived_key)

    def save_keys(self, key_type: str, keys: Dict[str, str]) -> bool:
        try:
            keys_json = json.dumps(keys)
            encrypted_data = self._fernet.encrypt(keys_json.encode())
            self._store.put(key_type, encrypted_data)
            logger.info("keys_saved", key_type=key_type)
            return True
        except Exception as e:
            logger.error("keys_save_failed", key_type=key_type, error=str(e))
            return False

    def load_keys(self, key_type: str) -> Optional[Dict[str, str]]:
        try:
            encrypted_data = self._store.get(key_type)
            if encrypted_data is None:
                return None
            decrypted_data = self._fernet.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode())
        except Exception as e:
            logger.error("keys_load_failed", key_type=key_type)
            raise SecretReadError("Secret storage unavailable or encryption key incorrect") from e

    def get_llm_credentials(self) -> Optional[Dict[str, str]]:
        """LLM provider credentials (provider/api_key/base_url/model/fallback_models).
        Falls back to the legacy `qwen` record so existing installs keep working."""
        return self.load_keys("llm") or self.load_keys("qwen")

    def get_github_token(self) -> Optional[str]:
        keys = self.load_keys("github")
        return keys.get("token") if keys else None

    def get_webhook_secret(self) -> Optional[str]:
        keys = self.load_keys("github")
        return keys.get("webhook_secret") if keys else None

    def get_storage_credentials(self) -> Optional[Dict[str, str]]:
        """S3-compatible audit-storage credentials. Falls back to the legacy
        `alibaba` record (same field names) so existing installs keep working
        without re-entering keys."""
        return self.load_keys("storage") or self.load_keys("alibaba")

    def get_admin_password_hash(self) -> Optional[str]:
        keys = self.load_keys("admin")
        return keys.get("password_hash") if keys else None

    def save_admin_password_hash(self, password_hash: str) -> bool:
        """Persist the admin password hash to disk (encrypted, like other
        keys) so it survives an app restart instead of living only in the
        in-memory settings object."""
        return self.save_keys("admin", {"password_hash": password_hash})

    def has_keys(self, key_type: str) -> bool:
        return self.load_keys(key_type) is not None

    def delete_keys(self, key_type: str) -> bool:
        try:
            self._store.delete(key_type)
            return True
        except Exception as e:
            logger.error("keys_delete_failed", key_type=key_type, error=str(e))
            return False


_key_manager: Optional[KeyManager] = None


def get_key_manager() -> KeyManager:
    """Singleton accessor for the KeyManager."""
    global _key_manager
    if _key_manager is None:
        from .settings import get_settings
        settings = get_settings()
        from .secret_store import build_secret_store
        _key_manager = KeyManager(
            keys_dir=settings.KEYS_DIR, secret_key=settings.SECRET_KEY, store=build_secret_store(settings)
        )
    return _key_manager
