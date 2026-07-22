"""Shared pytest fixtures for the DevInbox test suite."""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.database import Base


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def sample_issue_data():
    return {
        "repo_full_name": "test-owner/test-repo",
        "issue_number": 42,
        "title": "Bug: Login fails when password contains @ symbol",
        "body": "Steps to reproduce:\n1. Set password to Test@123\n2. Try login\n3. See 500 error",
        "author": "test-user",
        "labels": ["bug", "authentication"],
    }
