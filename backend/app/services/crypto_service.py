"""JWT session tokens for dashboard auth, plus password hashing and
Fernet encryption utilities."""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import hashlib
import hmac
import secrets
import base64
from cryptography.fernet import Fernet
import jwt  # PyJWT
import structlog

logger = structlog.get_logger()


class CryptoService:
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRATION_HOURS = 24

    def __init__(self, secret_key: str):
        self.secret_key = secret_key
        key_bytes = hashlib.sha256(secret_key.encode()).digest()
        self.fernet_key = base64.urlsafe_b64encode(key_bytes)
        self.fernet = Fernet(self.fernet_key)

    def create_access_token(self, data: Dict) -> str:
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + timedelta(hours=self.JWT_EXPIRATION_HOURS)
        to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc), "type": "access_token"})
        return jwt.encode(to_encode, self.secret_key, algorithm=self.JWT_ALGORITHM)

    def verify_token(self, token: str) -> Optional[Dict]:
        try:
            return jwt.decode(token, self.secret_key, algorithms=[self.JWT_ALGORITHM])
        except jwt.PyJWTError as e:
            logger.warning("token_verification_failed", error=str(e))
            return None

    # scrypt (memory-hard, stdlib): ~64 MB and tens of ms per guess, so a leaked
    # hash is expensive to brute-force. Format: scrypt$N$r$p$salt_hex$hash_hex
    _SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1

    def hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=self._SCRYPT_N, r=self._SCRYPT_R,
                                p=self._SCRYPT_P, maxmem=128 * 1024 * 1024, dklen=32)
        return f"scrypt${self._SCRYPT_N}${self._SCRYPT_R}${self._SCRYPT_P}${salt.hex()}${digest.hex()}"

    def verify_password(self, password: str, stored_hash: str) -> bool:
        try:
            if stored_hash.startswith("scrypt$"):
                _, n, r, p, salt_hex, hash_hex = stored_hash.split("$")
                digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r),
                                        p=int(p), maxmem=128 * 1024 * 1024, dklen=len(hash_hex) // 2)
                return hmac.compare_digest(digest.hex(), hash_hex)
            # Legacy "salt:sha256hex" hashes from earlier installs still verify.
            salt, hash_value = stored_hash.split(":")
            computed_hash = hashlib.sha256((salt + password).encode()).hexdigest()
            return hmac.compare_digest(computed_hash, hash_value)
        except (ValueError, AttributeError):
            return False

    def encrypt(self, plaintext: str) -> str:
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self.fernet.decrypt(ciphertext.encode()).decode()


_crypto_service: Optional[CryptoService] = None


def get_crypto_service() -> CryptoService:
    global _crypto_service
    if _crypto_service is None:
        from ..config.settings import get_settings
        _crypto_service = CryptoService(get_settings().SECRET_KEY)
    return _crypto_service
