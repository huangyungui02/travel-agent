"""Offline checks for native tools and Markdown memory."""

import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx2 as httpx
import openai
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from agent import build_agent


class AgentIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.steps = []
        self.requests = []
        self.citations = False
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        client = openai.AsyncOpenAI(api_key="offline", base_url="https://example.com/compatible-mode/v1", http_client=self.http)
        self.model = ChatOpenAI(
            model="qwen3.8-flash", api_key="offline", root_async_client=client,
            async_client=client.chat.completions, use_responses_api=True,
            extra_body={"enable_thinking": False}, disable_streaming=True, use_previous_response_id=False,
        )
        self.saver = InMemorySaver()
        self.agent = build_agent(self.model, [], checkpointer=self.saver)
        self.config = {"configurable": {"thread_id": "trip-a"}}

    async def asyncTearDown(self):
        await self.http.aclose()

    def respond(self, request):
        self.assertEqual(request.url.path, "/compatible-mode/v1/responses")
        payload = json.loads(request.content)
        self.requests.append(payload)
        self.assertFalse(payload["enable_thinking"])
        self.assertNotIn("previous_response_id", payload)
        self.assertNotEqual(payload.get("text", {}).get("format", {}).get("type"), "json_schema")
        names = {t.get("name") for t in payload["tools"]}
        self.assertTrue({"read_file", "write_file", "edit_file", "flyai", "current_date"} <= names)
        self.assertFalse(names & {"task", "execute", "get_travel_draft", "update_travel_requirements",
                                  "submit_travel_outline", "confirm_travel_outline"})
        self.assertEqual([t for t in payload["tools"] if t["type"] != "function"], [{"type": "web_search"}])
        step = self.steps.pop(0)
        index = len(self.requests)
        if isinstance(step, tuple):
            name, args = step[0], json.dumps(step[1])
            output = [{"type": "function_call", "id": f"fc_{index}", "call_id": f"call_{index}",
                       "name": name, "arguments": args, "status": "completed"}]
        else:
            annotations = ([{"type": "url_citation", "url": "https://example.com/", "title": "来源",
                             "start_index": 0, "end_index": 4}] if self.citations else [])
            output = [{"type": "message", "id": f"msg_{index}", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": step, "annotations": annotations}]}]
            if self.citations:
                output.insert(0, {"type": "web_search_call", "id": f"ws_{index}", "status": "completed",
                                  "action": {"type": "search", "query": "杭州旅游"}})
        return httpx.Response(200, json={
            "id": f"resp_{index}", "object": "response", "created_at": 0, "status": "completed",
            "error": None, "incomplete_details": None, "model": "qwen3.8-flash", "output": output,
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
        })

    async def turn(self, text, *steps, config=None):
        self.steps.extend(steps)
        result = await self.agent.ainvoke({"messages": [HumanMessage(content=text)]}, config or self.config)
        self.assertEqual(self.steps, [])
        return result

    def help_steps(self):
        return [
            ("read_file", {"file_path": "/skills/flyai/SKILL.md"}),
            ("read_file", {"file_path": "/skills/flyai/references/keyword-search.md"}),
            ("flyai", {"command": "keyword-search", "arguments": ["--help"]}),
            "旅行工具检查完成。",
        ]

    async def test_skill_cli_and_native_search(self):
        self.citations = True
        result = await self.turn("检查旅行工具", *self.help_steps())
        outputs = [m.text for m in result["messages"] if m.type == "tool"]
        self.assertIn("FlyAI", outputs[0])
        self.assertIn("--query", outputs[1])
        self.assertIn("Usage: flyai", outputs[2])
        self.assertIn("旅行工具检查完成", result["messages"][-1].text)
        self.assertIn("https://example.com/", json.dumps(result["messages"][-1].content))
        self.assertEqual(len(self.requests), 4)

    async def test_markdown_notes_restore_and_threads_are_isolated(self):
        note = "# 杭州旅行\n两位成人，预算共4000元。交通、酒店和每日骨架待用户确认。"
        await self.turn("记住旅行需求", ("write_file", {"file_path": "/travel-plan.md", "content": note}), "需求已记录。")
        self.agent = build_agent(self.model, [], checkpointer=self.saver)
        result = await self.turn(
            "预算改成3000元", ("read_file", {"file_path": "/travel-plan.md"}),
            ("edit_file", {"file_path": "/travel-plan.md", "old_string": "4000元", "new_string": "3000元"}),
            "预算已改为两人共3000元，其他需求保留。",
        )
        self.assertIn("4000元", [m.text for m in result["messages"] if m.type == "tool"][-2])
        self.assertNotIn("travel", result)
        result = await self.turn("查看笔记", ("read_file", {"file_path": "/travel-plan.md"}), "预算为3000元。")
        self.assertIn("3000元", result["messages"][-2].text)
        isolated = await self.turn("你好", "你好，想去哪里旅行？", config={"configurable": {"thread_id": "trip-b"}})
        self.assertNotIn("/travel-plan.md", isolated.get("files", {}))

    async def test_natural_confirmation_continues_detailed_planning(self):
        await self.turn(
            "给一个三天骨架", ("write_file", {"file_path": "/travel-plan.md", "content": "三天杭州行程，交通酒店和骨架待确认。"}),
            "请确认这套大交通、酒店和三天骨架，或提出修改。",
        )
        result = await self.turn(
            "好的，就按这个来，继续细化", ("read_file", {"file_path": "/travel-plan.md"}),
            ("edit_file", {"file_path": "/travel-plan.md", "old_string": "待确认", "new_string": "用户已确认"}),
            "详细行程：第一天湖滨慢走，第二天按区域游览，第三天预留返程余量。",
        )
        self.assertIn("详细行程", result["messages"][-1].text)
        system = json.dumps(self.requests[-1]["input"], ensure_ascii=False)
        self.assertNotIn("确认骨架 vN", system)
        self.assertNotIn("get_travel_draft", system)

    async def test_cli_rejects_non_search_command(self):
        with patch("agent.asyncio.create_subprocess_exec", new_callable=AsyncMock) as execute:
            result = await self.turn(
                "检查命令边界", ("flyai", {"command": "pay", "arguments": []}), "本次请求未执行。",
            )
            execute.assert_not_awaited()
            self.assertEqual(result["messages"][-2].status, "error")


if __name__ == "__main__":
    unittest.main()
