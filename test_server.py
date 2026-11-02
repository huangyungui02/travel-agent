"""Offline FastAPI integration tests with real SQLite checkpoints."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph import MessagesState, StateGraph, START, END

from server import create_app


class ServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "travel.sqlite"
        self.started = asyncio.Event()
        self.release = asyncio.Event()

        class State(MessagesState):
            files: dict

        async def factory(config, *, checkpointer):
            async def answer(state):
                self.started.set()
                text = state["messages"][-1].content
                if text == "wait":
                    await self.release.wait()
                if text == "error":
                    raise RuntimeError("offline failure")
                if text == "stream":
                    model = FakeListChatModel(responses=["流式答复"], sleep=0.01)
                    return {"messages": [await model.ainvoke(state["messages"])]}
                return {"messages": [AIMessage(content="答复：" + text)],
                        "files": {"/daily-itinerary.md": {"content": "# 杭州行程"}}}
            return (StateGraph(State).add_node("answer", answer)
                    .add_edge(START, "answer").add_edge("answer", END).compile(checkpointer=checkpointer))

        self.factory = factory
        await self.open()

    async def open(self):
        self.app = create_app(self.path, self.factory)
        self.lifecycle = self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def close(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)

    async def asyncTearDown(self):
        await self.close()
        self.directory.cleanup()

    async def create(self):
        return (await self.client.post("/threads")).json()["thread_id"]

    async def events(self, thread):
        response = await self.client.get(f"/threads/{thread}/events")
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    async def test_history_files_restart_isolation_and_delete(self):
        thread = await self.create()
        response = await self.client.post(f"/threads/{thread}/runs", json={"message": "杭州"})
        self.assertEqual(response.status_code, 200)
        snapshots = await self.events(thread)
        self.assertEqual(snapshots[-1]["status"], "success")
        self.assertEqual(snapshots[-1]["values"]["messages"][-1]["content"], "答复：杭州")
        await self.close()
        await self.open()
        saved = (await self.client.get(f"/threads/{thread}")).json()
        self.assertEqual(saved["values"]["files"]["/daily-itinerary.md"]["content"], "# 杭州行程")
        self.assertEqual(saved["status"], "idle")
        await self.client.post(f"/threads/{thread}/runs", json={"message": "修改"})
        self.assertEqual(len((await self.events(thread))[-1]["values"]["messages"]), 4)
        other = await self.create()
        self.assertEqual((await self.client.get(f"/threads/{other}")).json()["values"]["messages"], [])
        self.assertEqual(len((await self.client.post("/threads/search")).json()), 2)
        self.assertEqual((await self.client.delete(f"/threads/{thread}")).status_code, 200)
        self.assertIsNone(await self.app.state.saver.aget_tuple({"configurable": {"thread_id": thread}}))
        self.assertEqual((await self.client.get(f"/threads/{thread}")).status_code, 404)

    async def test_concurrency_cancel_and_restart_does_not_resume(self):
        thread = await self.create()
        await self.client.post(f"/threads/{thread}/runs", json={"message": "wait"})
        await asyncio.wait_for(self.started.wait(), 5)
        self.assertEqual((await self.client.post(f"/threads/{thread}/runs", json={"message": "duplicate"})).status_code, 409)
        self.assertEqual((await self.client.delete(f"/threads/{thread}")).status_code, 409)
        await self.client.post(f"/threads/{thread}/stop")
        self.assertEqual((await self.events(thread))[-1]["status"], "interrupted")
        await self.client.post(f"/threads/{thread}/runs", json={"message": "wait"})
        await self.close()
        await self.open()
        self.assertEqual((await self.client.get(f"/threads/{thread}")).json()["status"], "idle")
        runs = (await self.client.get(f"/threads/{thread}/runs")).json()
        self.assertTrue(all(run["status"] == "interrupted" for run in runs))
        # Simulate a crash that left a running row in SQLite.
        await self.app.state.db.execute("UPDATE runs SET status='running'")
        await self.app.state.db.commit()
        await self.close()
        await self.open()
        self.assertTrue(all(run["status"] == "interrupted" for run in (await self.client.get(f"/threads/{thread}/runs")).json()))

    async def test_token_stream_snapshots_and_reconnect(self):
        thread = await self.create()
        await self.client.post(f"/threads/{thread}/runs", json={"message": "stream"})
        snapshots = await self.events(thread)
        text = lambda state: (state["values"].get("messages") or [{}])[-1].get("content", "")
        self.assertTrue(any(text(state) == "流" for state in snapshots))
        self.assertEqual(text(snapshots[-1]), "流式答复")
        self.assertEqual(text((await self.events(thread))[-1]), "流式答复")

    async def test_real_harness_delegation_with_sqlite(self):
        from agent import build_agent
        from test_agent import AgentIntegrationTest
        fixture = AgentIntegrationTest()
        await fixture.asyncSetUp()
        try:
            async def factory(config, *, checkpointer):
                return build_agent(fixture.model, [], checkpointer=checkpointer)
            await self.close()
            self.factory = factory
            await self.open()
            thread = await self.create()
            fixture.steps.extend([
                ("task", {"subagent_type": "daily_planner", "description": "保存杭州行程"}),
                ("write_file", {"file_path": "/daily-itinerary.md", "content": "# 杭州行程"}),
                "已保存。", "行程已保存。",
            ])
            await self.client.post(f"/threads/{thread}/runs", json={"message": "规划杭州"})
            snapshots = await self.events(thread)
            self.assertEqual(snapshots[-1]["status"], "success")
            self.assertEqual(snapshots[-1]["values"]["files"]["/daily-itinerary.md"]["content"], "# 杭州行程")
            self.assertEqual(fixture.steps, [])
            await self.close()
            await self.open()
            saved = (await self.client.get(f"/threads/{thread}")).json()
            self.assertEqual(saved["values"]["messages"][-1]["content"][0]["text"], "行程已保存。")
            fixture.steps.extend([
                ("task", {"subagent_type": "daily_planner", "description": "修改上次行程标题"}),
                ("edit_file", {"file_path": "/daily-itinerary.md", "old_string": "杭州行程", "new_string": "杭州慢游"}),
                "已修改。", "修改已保存。",
            ])
            await self.client.post(f"/threads/{thread}/runs", json={"message": "修改标题"})
            revised = (await self.events(thread))[-1]
            self.assertEqual(revised["status"], "success")
            self.assertEqual(revised["values"]["files"]["/daily-itinerary.md"]["content"], "# 杭州慢游")
        finally:
            await fixture.asyncTearDown()

    async def test_error_and_validation(self):
        thread = await self.create()
        self.assertEqual((await self.client.post(f"/threads/{thread}/runs", json={"message": "  "})).status_code, 422)
        await self.client.post(f"/threads/{thread}/runs", json={"message": "error"})
        with self.assertLogs("server", level="ERROR"):
            snapshots = await self.events(thread)
        self.assertEqual(snapshots[-1]["status"], "error")
        self.assertTrue(snapshots[-1]["error"])
        self.assertEqual((await self.client.get(f"/threads/{thread}")).json()["status"], "idle")


if __name__ == "__main__":
    unittest.main()
