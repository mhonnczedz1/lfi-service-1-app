import asyncio
import logging
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from src.config import database_url, service_2_url

# Log to stdout at INFO. In Kubernetes stdout IS the log: kubectl logs reads
# it, so writing to a file would only hide it inside the container.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("service-1")

# IF NOT EXISTS makes this safe to run on every startup, which it is, since
# lifespan applies it each time. See the note below the code block about why
# this belongs in a migration tool in a real service.
SCHEMA = """
CREATE TABLE IF NOT EXISTS computations (
    id          SERIAL       PRIMARY KEY,
    input_value INTEGER      NOT NULL,
    result      INTEGER      NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
)
"""


async def connect_with_retry(
    dsn: str, attempts: int = 30, delay: float = 2.0
) -> asyncpg.Pool:
    """Postgres is frequently still starting when this pod is scheduled.

    Kubernetes would eventually fix that for us by restarting a crashed
    pod, but retrying in-process turns a noisy CrashLoopBackOff into a
    quiet 60-second startup.
    """
    # 30 attempts x 2s = up to a minute of patience, which comfortably covers
    # a cold Postgres initialising its data directory on first boot.
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            # min_size=1 so the pool is usable immediately; max_size=5 because
            # two replicas x 5 is well within Postgres' default 100 limit.
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
            logger.info("Connected to Postgres on attempt %s.", attempt)
            return pool
        # OSError covers DNS and TCP failures (Postgres not up yet);
        # PostgresError covers it being up but not accepting this database.
        # Deliberately narrow: a wrong password should not be retried 30 times.
        except (OSError, asyncpg.PostgresError) as error:
            last_error = error
            logger.warning(
                "Database not ready (attempt %s/%s): %s", attempt, attempts, error
            )
            await asyncio.sleep(delay)
    # `from last_error` preserves the original traceback, so the logs show what
    # actually went wrong rather than just "could not reach".
    raise RuntimeError(
        f"Could not reach the database after {attempts} attempts."
    ) from last_error


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup before the yield, shutdown after it.

    Connections are opened once here rather than per request. Opening a pool
    per request would work and would also be catastrophically slow.
    """
    app.state.pool = await connect_with_retry(database_url())
    async with app.state.pool.acquire() as connection:
        await connection.execute(SCHEMA)
    # One shared client, reused across requests, so connections to service-2
    # are pooled and kept alive. The 5s timeout is what turns a hung worker
    # into a 502 rather than a request that never returns.
    app.state.http = httpx.AsyncClient(base_url=service_2_url(), timeout=5.0)
    logger.info("Startup complete.")
    yield
    # Ordered teardown. Skipping these leaks sockets on every pod restart.
    await app.state.http.aclose()
    await app.state.pool.close()


app = FastAPI(title="service-1-gateway", version="1.0.0", lifespan=lifespan)


# These two exist as dependencies rather than direct app.state reads so
# that tests can override them. See tests/test_main.py.
#
# Reading request.app.state instead of the module-level `app` is what makes the
# override work: the test swaps the function, and nothing here needs to change.
def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def get_worker(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


class ComputeRequest(BaseModel):
    value: int


class ComputeResponse(BaseModel):
    # Both service names are in the response on purpose: it is the evidence
    # that the request really traversed two pods, not one.
    gateway: str
    worker: str
    input: int
    result: int
    persisted_id: int


class HistoryEntry(BaseModel):
    id: int
    input_value: int
    result: int


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness. Deliberately dependency-free: a database blip should not
    cause Kubernetes to kill an otherwise healthy pod."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, str]:
    """Readiness. Does touch the database, because a pod that cannot reach
    Postgres should be pulled out of the Service's endpoint list."""
    # The cheapest possible round trip that proves the connection is live.
    # If this raises, FastAPI returns 500 and the readiness probe fails, which
    # is the intended behaviour: remove this pod from the Service endpoints
    # without restarting it.
    await pool.fetchval("SELECT 1")
    return {"status": "ready"}


@app.post("/compute", response_model=ComputeResponse)
async def compute(
    body: ComputeRequest,
    worker: httpx.AsyncClient = Depends(get_worker),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ComputeResponse:
    """The full path the architecture exists to demonstrate:
    ingress -> service-1 -> service-2 over cluster DNS -> Postgres.
    """
    # Call the worker FIRST, persist second. The ordering is the contract:
    # a failed worker call must leave no row behind. The 502 test pins it.
    try:
        response = await worker.post("/process", json={"value": body.value})
        # httpx does not raise on 4xx/5xx by default, so this line is what
        # turns the broken_worker fixture's 503 into an exception.
        response.raise_for_status()
    # httpx.HTTPError is the base class, so this covers timeouts, connection
    # failures, and the status error above in one handler.
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502, detail=f"service-2 unreachable: {error}"
        ) from error

    payload = response.json()

    # $1/$2 are asyncpg's placeholders. Values are passed separately, so the
    # driver parameterises the query and string formatting never touches SQL.
    # RETURNING id hands back the new primary key in the same round trip.
    persisted_id = await pool.fetchval(
        "INSERT INTO computations (input_value, result) VALUES ($1, $2) RETURNING id",
        body.value,
        payload["result"],
    )

    return ComputeResponse(
        gateway="service-1",
        # Read from the worker's response rather than hardcoded, so this field
        # is meaningful evidence about who actually answered.
        worker=payload["service"],
        input=body.value,
        result=payload["result"],
        persisted_id=persisted_id,
    )


@app.get("/history", response_model=list[HistoryEntry])
async def history(pool: asyncpg.Pool = Depends(get_pool)) -> list[HistoryEntry]:
    # DESC + LIMIT 10: newest first, bounded. An unbounded SELECT would be a
    # slow surprise the first time the table is large.
    rows = await pool.fetch(
        "SELECT id, input_value, result FROM computations ORDER BY id DESC LIMIT 10"
    )
    # asyncpg returns Record objects, not dicts. dict(row) converts, then **
    # unpacks into the model, which validates the shape on the way out.
    return [HistoryEntry(**dict(row)) for row in rows]
