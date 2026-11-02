"""Single-process personal deployment: FastAPI, SQLite and resumable UI streams."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiosqlite
from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent import build_agent, graph


def now():
    return datetime.now(timezone.utc).isoformat()


def config(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def public_values(values):
    return jsonable_encoder({key: values[key] for key in ("messages", "files") if key in values})


@dataclass
class LiveRun:
    task: asyncio.Task | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    version: int = 0
    values: dict = field(default_factory=dict)
    error: str | None = None
    status: str = "running"

    async def publish(self):
        async with self.changed:
            self.version += 1
            self.changed.notify_all()


class MessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=32000)


def create_app(database_path=None, agent_factory=graph):
    live: dict[str, LiveRun] = {}

    @asynccontextmanager
    async def lifespan(app):
        path = Path(database_path or os.getenv("TRAVEL_DATABASE", "data/travel.sqlite"))
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(path) as db, AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await saver.conn.execute("PRAGMA busy_timeout=5000")
            await saver.setup()
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                UPDATE runs SET status='interrupted' WHERE status='running';
            """)
            await db.commit()
            app.state.db, app.state.saver = db, saver
            # Reading state needs the graph's channel schema, including delta channels.
            # This model is never invoked and does not load remote MCP tools.
            app.state.reader = build_agent(
                ChatOpenAI(model="state-reader", api_key="unused"), [], checkpointer=saver,
            )
            try:
                yield
            finally:
                tasks = [run.task for run in live.values() if run.task and not run.task.done()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                live.clear()

    app = FastAPI(title="Travel Agent", lifespan=lifespan)

    async def require_thread(thread_id):
        async with app.state.db.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise HTTPException(404, "会话不存在")
        return dict(row)

    async def saved_values(thread_id):
        snapshot = await app.state.reader.aget_state(config(thread_id))
        return {"messages": [], **snapshot.values}

    async def thread_view(thread_id):
        row = await require_thread(thread_id)
        run = live.get(thread_id)
        values = run.values if run and run.status == "running" else await saved_values(thread_id)
        return {**row, "values": public_values(values),
                "status": "busy" if run and run.status == "running" else "idle"}

    @app.get("/ok")
    async def health():
        return {"ok": True}

    @app.post("/threads")
    async def create_thread():
        thread_id, timestamp = str(uuid4()), now()
        await app.state.db.execute("INSERT INTO threads VALUES (?, ?, ?)", (thread_id, timestamp, timestamp))
        await app.state.db.commit()
        return await thread_view(thread_id)

    @app.post("/threads/search")
    async def search_threads():
        async with app.state.db.execute("SELECT thread_id FROM threads ORDER BY updated_at DESC LIMIT 50") as cursor:
            rows = await cursor.fetchall()
        return [await thread_view(row["thread_id"]) for row in rows]

    @app.get("/threads/{thread_id}")
    async def get_thread(thread_id: str):
        return await thread_view(thread_id)

    @app.delete("/threads/{thread_id}")
    async def delete_thread(thread_id: str):
        await require_thread(thread_id)
        run = live.get(thread_id)
        if run and run.status == "running":
            raise HTTPException(409, "请先停止正在运行的任务")
        await app.state.saver.adelete_thread(thread_id)
        await app.state.db.execute("DELETE FROM runs WHERE thread_id=?", (thread_id,))
        await app.state.db.execute("DELETE FROM threads WHERE thread_id=?", (thread_id,))
        await app.state.db.commit()
        live.pop(thread_id, None)
        return {"ok": True}

    async def execute(thread_id, run_id, message, run):
        run.started.set()
        status = "error"
        try:
            agent = await agent_factory(config(thread_id), checkpointer=app.state.saver)
            chunks = {}
            async for namespace, mode, data in agent.astream(
                {"messages": [{"role": "user", "content": message}]}, config(thread_id),
                stream_mode=["values", "messages"], subgraphs=True,
            ):
                if namespace:
                    continue
                if mode == "values":
                    run.values = data
                    chunks.clear()
                elif mode == "messages":
                    chunk, _ = data
                    if chunk.type != "AIMessageChunk" or not chunk.id:
                        continue
                    chunks[chunk.id] = chunks[chunk.id] + chunk if chunk.id in chunks else chunk
                    streamed = chunks[chunk.id].model_dump()
                    streamed["type"] = "ai"
                    messages = list(run.values.get("messages", []))
                    messages = [m for m in messages if (m.get("id") if isinstance(m, dict) else m.id) != chunk.id]
                    run.values = {**run.values, "messages": [*messages, streamed]}
                await run.publish()
            status = "success"
        except asyncio.CancelledError:
            status = "interrupted"
        except Exception:
            logging.getLogger(__name__).exception("Agent run failed: %s", run_id)
            status = "error"
            run.error = "旅行规划暂时失败，请检查服务日志后重试。"
        finally:
            # Only durable graph state is authoritative after a run ends.
            run.values = await saved_values(thread_id)
            await app.state.db.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
            await app.state.db.execute("UPDATE threads SET updated_at=? WHERE thread_id=?", (now(), thread_id))
            await app.state.db.commit()
            run.status = status
            await run.publish()

    @app.post("/threads/{thread_id}/runs")
    async def start_run(thread_id: str, body: MessageInput):
        await require_thread(thread_id)
        if not body.message.strip():
            raise HTTPException(422, "消息不能为空")
        # Reserve before the next await: concurrent requests cannot start a second run.
        previous = live.get(thread_id)
        if previous and previous.status == "running":
            raise HTTPException(409, "当前会话正在生成回复")
        run = LiveRun()
        live[thread_id] = run
        run_id = str(uuid4())
        try:
            run.values = await saved_values(thread_id)
            await app.state.db.execute("INSERT INTO runs VALUES (?, ?, 'running', ?)", (run_id, thread_id, now()))
            await app.state.db.commit()
            run.task = asyncio.create_task(execute(thread_id, run_id, body.message.strip(), run))
            await run.started.wait()
        except BaseException:
            live.pop(thread_id, None)
            raise
        return {"run_id": run_id, "status": "running"}

    @app.get("/threads/{thread_id}/runs")
    async def list_runs(thread_id: str):
        await require_thread(thread_id)
        async with app.state.db.execute("SELECT * FROM runs WHERE thread_id=? ORDER BY created_at DESC", (thread_id,)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    @app.post("/threads/{thread_id}/stop")
    async def stop(thread_id: str):
        await require_thread(thread_id)
        run = live.get(thread_id)
        if run and run.task and not run.task.done() and not run.task.cancelling():
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
        return {"ok": True}

    @app.get("/threads/{thread_id}/events")
    async def events(thread_id: str):
        await require_thread(thread_id)
        run = live.get(thread_id)

        async def generate():
            version = -1
            while run:
                async with run.changed:
                    if version == run.version and run.status == "running":
                        try:
                            await asyncio.wait_for(run.changed.wait(), 15)
                        except TimeoutError:
                            pass
                    version = run.version
                    payload = {"values": public_values(run.values), "status": run.status, "error": run.error}
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                if run.status != "running":
                    return
            if not run:
                yield "data: " + json.dumps({"values": public_values(await saved_values(thread_id)), "status": "idle"}) + "\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
