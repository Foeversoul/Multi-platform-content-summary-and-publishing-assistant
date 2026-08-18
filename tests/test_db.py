import pytest
from sqlalchemy import select

from app.storage.db import session_scope
from app.storage.models import Source


def test_session_scope_commits(session_factory):
    with session_scope(session_factory) as session:
        session.add(Source(external_id="x", name="X", type="rss", url="https://x/feed"))
    session = session_factory()
    assert session.scalar(select(Source).where(Source.external_id == "x")) is not None
    session.close()


def test_session_scope_rolls_back_on_error(session_factory):
    with pytest.raises(RuntimeError):
        with session_scope(session_factory) as session:
            session.add(Source(external_id="y", name="Y", type="rss", url="https://y/feed"))
            raise RuntimeError("boom")
    session = session_factory()
    assert session.scalar(select(Source).where(Source.external_id == "y")) is None
    session.close()
