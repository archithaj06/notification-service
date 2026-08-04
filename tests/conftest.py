"""
Test strategy note: unit/integration tests run against an in-memory SQLite DB
and fakeredis instead of the real Postgres/Redis used in local dev and
docker-compose. This keeps the suite fast and hermetic (no external services
required to run `pytest`), and is safe here because our models avoid any
Postgres-only types (no JSONB/native ENUM). The real Postgres+Redis stack is
exercised via manual end-to-end testing and is what docker-compose runs.
"""
import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.models import Base


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session(db_engine) -> Session:
    TestingSessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture()
def client(db_engine, db_session, fake_redis, monkeypatch):
    """
    TestClient wired to the SQLite test DB. Also:
      - Swaps the rate limiter's and circuit breaker's Redis client for fakeredis
      - Makes queue.enqueue run the worker job synchronously in-process,
        instead of requiring a live RQ worker, so API tests can assert on
        final delivery state without sleeping/polling for a background worker.
    """
    from app.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    import app.api.routes.notifications as notif_routes
    import app.services.channels.factory as channel_factory

    notif_routes.rate_limiter.redis = fake_redis
    channel_factory.circuit_breaker.redis = fake_redis

    class _SyncQueue:
        """
        Test double for RQ's Queue. Jobs are appended to a shared pending list
        and drained iteratively (not recursively) by whichever call started
        the drain -- this mirrors how real RQ processes jobs one at a time as
        separate, independent executions. Running a scheduled retry as a
        *nested* call inside the job that scheduled it (naive recursion) would
        let the outer job's now-stale in-memory objects overwrite the inner
        job's later commit when the outer frame's own tail code runs after
        the nested call returns -- a bug class that doesn't exist in
        production, where each job is a fresh, sequential invocation.
        """

        def __init__(self, pending_jobs: list, draining: list):
            self._pending_jobs = pending_jobs
            self._draining = draining  # single-element mutable flag

        def enqueue(self, func_path, *args, **kwargs):
            self._pending_jobs.append((func_path, args))
            self._drain()

        def enqueue_in(self, delay, func_path, *args, **kwargs):
            # Tests run retries immediately rather than waiting out the real
            # backoff delay -- this still exercises the exact same retry code
            # path, just without slowing the suite down.
            self._pending_jobs.append((func_path, args))
            self._drain()

        def _drain(self):
            if self._draining[0]:
                return  # an outer call is already draining; it will pick this job up
            self._draining[0] = True
            try:
                while self._pending_jobs:
                    func_path, args = self._pending_jobs.pop(0)
                    _run_job(func_path, args)
            finally:
                self._draining[0] = False

    def _run_job(func_path, args):
        import importlib

        module_path, func_name = func_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)
        # The worker module does `from app.core.db import SessionLocal`, which
        # binds its own local name at import time -- patching app.core.db's
        # attribute alone would NOT affect that already-bound reference, so we
        # patch the worker module's own `SessionLocal` name directly.
        import app.workers.notification_worker as worker_module

        TestWorkerSessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
        original_session_local = worker_module.SessionLocal
        worker_module.SessionLocal = TestWorkerSessionLocal
        try:
            func(*args)
        finally:
            worker_module.SessionLocal = original_session_local
            db_session.expire_all()  # pick up commits made by the worker's session

    _pending_jobs: list = []
    _draining = [False]
    monkeypatch.setattr(
        "app.services.notification_service.get_queue_for_priority",
        lambda p: _SyncQueue(_pending_jobs, _draining),
    )
    monkeypatch.setattr(
        "app.workers.notification_worker.get_queue_for_priority",
        lambda p: _SyncQueue(_pending_jobs, _draining),
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
