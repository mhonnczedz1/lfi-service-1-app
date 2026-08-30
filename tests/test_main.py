import httpx
import pytest

# get_pool and get_worker are imported not to call them, but to use them as
# keys into app.dependency_overrides below.
from src.main import app, get_pool, get_worker


class FakePool:
    """Stands in for an asyncpg pool. Records what would have been written."""

    def __init__(self):
        # The assertion surface. Tests check these instead of querying a real
        # database, which is what keeps the suite runnable in CI with no
        # services at all.
        self.inserted: list[tuple] = []
        self.rows: list[dict] = []

    async def fetchval(self, query: str, *args):
        # Crude query sniffing, but enough: /readyz probes with SELECT 1 and
        # /compute inserts. A real fake would parse SQL; this is a stub.
        if query.strip().startswith("SELECT 1"):
            return 1
        self.inserted.append(args)
        # Stands in for the RETURNING id, hence persisted_id == 1 below.
        return 1

    async def fetch(self, query: str, *args):
        return self.rows


# ---------------------------------------------------------------------------
# Two canned service-2 responses. These are httpx handler functions: given a
# request, return a response, with no network involved.
# ---------------------------------------------------------------------------
def healthy_worker(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"service": "service-2", "input": 21, "result": 42})


def broken_worker(request: httpx.Request) -> httpx.Response:
    # 503 rather than a connection error. Either would do; this proves
    # raise_for_status is what catches it.
    return httpx.Response(503, text="worker is down")


def build_client(worker_handler):
    """Wire up an app whose two external dependencies are fakes.

    Returns the test client and the FakePool, because tests need to assert
    against the pool after the request completes.
    """
    pool = FakePool()
    # MockTransport intercepts outbound requests from service-1 to service-2.
    worker = httpx.AsyncClient(
        transport=httpx.MockTransport(worker_handler),
        base_url="http://service-2:8000",
    )
    # This is the mechanism the whole test file rests on. FastAPI resolves
    # Depends(get_pool) by looking in this dict first, so the app under test
    # receives fakes without any change to production code.
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_worker] = lambda: worker
    # ASGITransport skips lifespan, so connect_with_retry never runs and no
    # real database connection is ever attempted.
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test"), pool


@pytest.fixture(autouse=True)
def clear_overrides():
    # autouse means this wraps every test in the file without being requested.
    # The body before yield is setup (nothing), after yield is teardown.
    yield
    # dependency_overrides lives on the module-level `app`, so it leaks between
    # tests unless cleared. Skipping this makes tests pass or fail depending on
    # execution order, which is a miserable bug to chase.
    app.dependency_overrides.clear()


async def test_healthz_needs_no_dependencies():
    # Note: no build_client, so no overrides are installed. This proves
    # /healthz works with nothing wired up, which is exactly what a liveness
    # probe has to do.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_touches_the_database():
    client, _ = build_client(healthy_worker)
    async with client:
        response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_compute_calls_the_worker_and_persists_the_result():
    client, pool = build_client(healthy_worker)
    async with client:
        response = await client.post("/compute", json={"value": 21})

    assert response.status_code == 200
    body = response.json()
    assert body["gateway"] == "service-1"
    # "service-2" here came from the worker's response body, not from a
    # constant in service-1. That is what makes this an integration assertion.
    assert body["worker"] == "service-2"
    assert body["result"] == 42
    assert body["persisted_id"] == 1

    # The write actually happened, with the worker's result, not a local one.
    # (21, 42): the input and the worker's answer, in that column order.
    assert pool.inserted == [(21, 42)]


async def test_compute_returns_502_when_the_worker_is_unreachable():
    client, pool = build_client(broken_worker)
    async with client:
        response = await client.post("/compute", json={"value": 21})

    # 502 Bad Gateway, not 500. The gateway is fine; its upstream is not, and
    # the status code should say which.
    assert response.status_code == 502
    assert "service-2" in response.json()["detail"]

    # Nothing is persisted when the upstream call fails.
    # This is the assertion that actually earns its keep: it pins the ordering
    # in compute(), where the worker call must precede the insert.
    assert pool.inserted == []


async def test_history_returns_recent_rows():
    client, pool = build_client(healthy_worker)
    # Preload the fake so /history has something to return.
    pool.rows = [{"id": 2, "input_value": 5, "result": 10}]
    async with client:
        response = await client.get("/history")

    assert response.status_code == 200
    assert response.json() == [{"id": 2, "input_value": 5, "result": 10}]
