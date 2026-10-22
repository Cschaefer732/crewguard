import pytest
from sqlmodel import Session

from crewguard.db import init_db, make_engine


@pytest.fixture
def engine():
    eng = make_engine(":memory:")
    init_db(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
