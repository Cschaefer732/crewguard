from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select

from crewguard.db import make_engine, session_scope
from crewguard.models import Config


def test_session_scope_commits(engine):
    with session_scope(engine) as s:
        s.add(Config(key="k", value="v"))
    with Session(engine) as s:
        assert s.get(Config, "k").value == "v"


def test_session_scope_rolls_back_on_error(engine):
    try:
        with session_scope(engine) as s:
            s.add(Config(key="k", value="v"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with Session(engine) as s:
        assert s.exec(select(Config)).all() == []


def test_memory_engine_shares_one_connection_across_threads(engine):
    """StaticPool regression: without it, another thread sees an empty :memory: db."""
    with session_scope(engine) as s:
        s.add(Config(key="k", value="v"))

    def read():
        with Session(engine) as s:
            return s.get(Config, "k").value

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(read).result() == "v"


def test_file_engine_uses_path(tmp_path):
    eng = make_engine(str(tmp_path / "x.db"))
    assert "x.db" in str(eng.url)
