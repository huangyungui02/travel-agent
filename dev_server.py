"""Local dev policy: keep conversations, but never resume abandoned runs."""

from contextlib import asynccontextmanager

from starlette.applications import Starlette


async def cancel_unfinished_runs(reason: str) -> None:
    # These runtime APIs are specific to the local `langgraph dev` server.
    from langgraph_runtime_inmem.database import connect
    from langgraph_runtime_inmem.ops import Runs

    async with connect() as conn:
        if any(run["status"] in {"pending", "running"} for run in conn.store["runs"]):
            await Runs.cancel(conn, status="all", action="interrupt", reason=reason)


@asynccontextmanager
async def lifespan(app):
    # The runtime has loaded its database; its queued worker task has not run yet.
    await cancel_unfinished_runs("Local dev restarted; abandoned runs are cancelled.")
    try:
        yield
    finally:
        await cancel_unfinished_runs("Local dev stopped; unfinished runs are cancelled.")


app = Starlette(lifespan=lifespan)
