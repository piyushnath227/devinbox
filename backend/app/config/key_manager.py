"""Encrypted storage and retrieval of API keys for Qwen Cloud and GitHub.

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

logger = structlog.get_logger()


class KeyManager:
    """Manages encrypted storage and retrieval of API keys."""

    SALT_FILE_NAME = ".salt"
    SALT_BYTES = 16

    def __init__(self, keys_dir: str, secret_key: str):
        self.keys_dir = Path(keys_dir)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = self._create_fernet(secret_key)
        logger.info("key_manager_initialized", keys_dir=str(self.keys_dir))

    def _get_or_create_salt(self) -> bytes:
        """Load this installation's PBKDF2 salt, generating and persisting a
        random one on first run. A per-installation random salt (rather than
        one hardcoded in source) is required for PBKDF2 to provide its
        intended protection."""
        salt_file = self.keys_dir / self.SALT_FILE_NAME
        if salt_file.exists():
            return salt_file.read_bytes()
        salt = os.urandom(self.SALT_BYTES)
        salt_file.write_bytes(salt)
        try:
            os.chmod(salt_file, 0o600)
        except OSError:
            pass
        logger.info("key_manager_salt_generated", salt_file=str(salt_file))
        return salt

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
            key_file = self.keys_dir / f"{key_type}.enc"
            with open(key_file, "wb") as f:
                f.write(encrypted_data)
            logger.info("keys_saved", key_type=key_type)
            return True
        except Exception as e:
            logger.error("keys_save_failed", key_type=key_type, error=str(e))
            return False

    def load_keys(self, key_type: str) -> Optional[Dict[str, str]]:
        try:
            key_file = self.keys_dir / f"{key_type}.enc"
            if not key_file.exists():
                return None
            with open(key_file, "rb") as f:
                encrypted_data = f.read()
            decrypted_data = self._fernet.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode())
        except Exception as e:
            logger.error("keys_load_failed", key_type=key_type, error=str(e))
            return None

    def get_qwen_api_key(self) -> Optional[str]:
        keys = self.load_keys("qwen")
        return keys.get("api_key") if keys else None

    def get_qwen_base_url(self) -> Optional[str]:
        keys = self.load_keys("qwen")
        return keys.get("base_url") if keys else None

    def get_qwen_model(self) -> Optional[str]:
        keys = self.load_keys("qwen")
        return keys.get("model") if keys else None

    def get_github_token(self) -> Optional[str]:
        keys = self.load_keys("github")
        return keys.get("token") if keys else None

    def get_alibaba_credentials(self) -> Optional[Dict[str, str]]:
        return self.load_keys("alibaba")

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
            key_file = self.keys_dir / f"{key_type}.enc"
            if key_file.exists():
                key_file.unlink()
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
        _key_manager = KeyManager(keys_dir=settings.KEYS_DIR, secret_key=settings.SECRET_KEY)
    return _key_manager
