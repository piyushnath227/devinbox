"""ADMIN_BOOTSTRAP_PASSWORD closes the first-run takeover window."""

from types import SimpleNamespace

import pytest

from app.bootstrap import bootstrap_admin
from app.services.crypto_service import CryptoService


class FakeKM:
    def __init__(self, existing=None): self.hash = existing
    def get_admin_password_hash(self): return self.hash
    def save_admin_password_hash(self, h): self.hash = h; return True


crypto = CryptoService("unit-test-secret-key-that-is-long-enough-32b")


def cfg(pw="", debug=False): return SimpleNamespace(ADMIN_BOOTSTRAP_PASSWORD=pw, DEBUG=debug)


def test_bootstraps_when_unclaimed_and_password_verifies():
    km = FakeKM()
    assert bootstrap_admin(km, crypto, cfg("a-long-operator-password")) == "bootstrapped"
    assert crypto.verify_password("a-long-operator-password", km.hash)
    assert not crypto.verify_password("attacker-guess", km.hash)


def test_never_overwrites_an_existing_admin():
    km = FakeKM(existing="existing-hash")
    assert bootstrap_admin(km, crypto, cfg("a-different-long-password")) == "existing"
    assert km.hash == "existing-hash"


def test_production_unclaimed_is_rejected():
    km = FakeKM()
    with pytest.raises(RuntimeError):
        bootstrap_admin(km, crypto, cfg(""))
    assert bootstrap_admin(km, crypto, cfg("", debug=True)) == "unclaimed"


def test_short_bootstrap_password_is_rejected():
    with pytest.raises(RuntimeError):
        bootstrap_admin(FakeKM(), crypto, cfg("short"))
