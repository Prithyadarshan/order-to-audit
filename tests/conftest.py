import os
import tempfile

# Must be set before the app is imported so the engine points at a throwaway DB.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
# Set TEST_DATABASE_URL to run the same suite on PostgreSQL.
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{_tmp.name}"

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
