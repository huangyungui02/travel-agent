"""Offline checks for native tools, Markdown files, and persistent delegation."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx2 as httpx
import openai
from langchain.tools import tool
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from pydantic import ValidationError

from agent import build_agent
from itinerary import Itinerary


def sample_itinerary():
    days = []
    for number, name in enumerate(["西湖", "灵隐登山", "河坊街"], 1):
        days.append({
            "date": f"2026-11-0{number}", "title": name,
            "items": [
                {"kind": "place", "id": f"d{number}-sight", "start_time": "11:00:00", "end_time": "12:00:00",
                 "activity": f"游览{name}", "description": "开放时间待核实", "cost": 0.0,
                 "links": [{"title": "官网", "url": "https://example.com/"}],
                 "place_name": name, "coordinates": {"longitude": 120.15, "latitude": 30.25}},
                {"kind": "route", "id": f"d{number}-route", "start_time": "12:00:00", "end_time": "12:20:00",
                 "activity": "步行至餐厅", "description": "沿湖步行", "cost": 0.0, "links": [],
                 "from_stop_id": f"d{number}-sight", "to_stop_id": f"d{number}-restaurant", "transport_modes": ["walking"]},
                {"kind": "place", "id": f"d{number}-restaurant", "start_time": "12:20:00", "end_time": "13:00:00",
                 "activity": "吃午餐", "description": "两人合计估算", "cost": 100.0, "links": [],
                 "place_name": "湖滨就餐区域", "coordinates": None},
            ],
        })
    return {"title": "杭州三日游", "travelers": 2, "days": days}


def sample_plan(second_day="灵隐登山"):
    return (
        "杭州三日游，2026-11-01至11-03，两位成人。"
        f"三天分别游览西湖、{second_day}、河坊街。"
        "每天11:00-12:00游览（免费），12:00-12:20沿湖步行至餐厅（免费），"
        "12:20-13:00在湖滨就餐区域吃午餐（两人合计估算100元）。"
        "景点高德经度120.15、纬度30.25，开放时间待核实，官网https://example.com/；餐饮区域坐标待核实。"
    )


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
        # DashScope Responses only accepts required mode with exactly one tool.
        if payload.get("tool_choice") == "required" and len(payload["tools"]) != 1:
            return httpx.Response(400, json={"error": {"message": "Please set one tool in required mode.",
                                                       "type": "InvalidParameter", "code": "InvalidParameter"}})
        names = {t.get("name") for t in payload["tools"]}
        self.assertNotIn("ConversionResult", names)
        self.assertNotEqual(payload.get("tool_choice"), "required")
        self.assertTrue({"read_file", "write_file", "edit_file", "flyai", "current_date"} <= names)
        self.assertNotIn("execute", names)
        self.assertNotIn("submit_itinerary", names)
        self.assertNotIn("read_itinerary", names)
        task_tools = [t for t in payload["tools"] if t.get("name") == "task"]
        if task_tools:
            self.assertNotIn("submit_itinerary", names)
            self.assertIn("- daily_planner:", task_tools[0]["description"])
            self.assertNotIn("- general-purpose:", task_tools[0]["description"])
        self.assertEqual([t for t in payload["tools"] if t["type"] != "function"], [{"type": "web_search"}])
        step = self.steps.pop(0)
        index = len(self.requests)
        if isinstance(step, (tuple, list)):
            calls = [step] if isinstance(step, tuple) else step
            output = []
            for offset, (name, args) in enumerate(calls):
                self.assertIn(name, names)
                output.append({"type": "function_call", "id": f"fc_{index}_{offset}",
                               "call_id": f"call_{index}_{offset}", "name": name,
                               "arguments": json.dumps(args), "status": "completed"})
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

    async def test_markdown_delivery_revision_and_thread_isolation(self):
        from deepagents.backends.utils import file_data_to_string
        ITINERARY_PATH = "/daily-itinerary.md"

        original = "# 杭州三日游\n\n| 时间 | 活动 | 描述 | 链接 | 预计费用 |\n|---|---|---|---|---|\n| 11:00–12:00 | 西湖 | 游览 | — | ¥0 |"
        outline = "已确认杭州三天，第一天西湖，第二天登山，第三天返程。"
        result = await self.turn(
            "请细化",
            ("task", {"subagent_type": "daily_planner", "description": outline}),
            ("write_file", {"file_path": ITINERARY_PATH, "content": original}),
            "完整行程已保存。",
            ("read_file", {"file_path": "/daily-itinerary.md"}),
            original,
        )
        self.assertEqual(file_data_to_string(result["files"][ITINERARY_PATH]), original)
        self.assertIn("西湖", result["messages"][-2].text)
        self.assertNotIn("itinerary", result)
        self.agent = build_agent(self.model, [], checkpointer=self.saver)
        revised = original.replace("西湖", "浙江省博物馆")
        result = await self.turn(
            "改成博物馆",
            ("task", {"subagent_type": "daily_planner", "description": "改成浙江省博物馆，其余保留。"}),
            ("edit_file", {"file_path": ITINERARY_PATH, "old_string": "西湖", "new_string": "浙江省博物馆"}),
            "修改已保存。",
            ("read_file", {"file_path": "/daily-itinerary.md"}),
            revised,
        )
        self.assertIn("浙江省博物馆", result["messages"][-2].text)
        self.assertEqual(file_data_to_string(result["files"][ITINERARY_PATH]), revised)
        self.assertIn(outline, json.dumps(self.requests[-4]["input"], ensure_ascii=False))
        result = await self.turn("读取行程", ("read_file", {"file_path": "/daily-itinerary.md"}), "尚未保存行程。",
                                 config={"configurable": {"thread_id": "trip-b"}})
        self.assertIn("not found", result["messages"][-2].text)
        self.assertNotIn(ITINERARY_PATH, result.get("files", {}))

    async def test_unfinished_revision_preserves_saved_file(self):
        from deepagents.backends.utils import file_data_to_string
        ITINERARY_PATH = "/daily-itinerary.md"

        await self.turn(
            "生成初稿",
            ("task", {"subagent_type": "daily_planner", "description": "已确认行程"}),
            ("write_file", {"file_path": ITINERARY_PATH, "content": "# 原始完整行程"}), "行程已保存。",
            ("read_file", {"file_path": "/daily-itinerary.md"}), "原始行程。",
        )
        result = await self.turn(
            "换酒店",
            ("task", {"subagent_type": "daily_planner", "description": "请更换酒店"}),
            "请先确认酒店分店，暂未修改行程。",
            "请确认酒店分店。",
        )
        self.assertIn("请先确认酒店分店", result["messages"][-2].text)
        self.assertEqual(file_data_to_string(result["files"][ITINERARY_PATH]), "# 原始完整行程")

    async def test_main_and_subagent_each_execute_at_most_three_tools(self):
        for role in ("main", "daily_planner"):
            with self.subTest(role=role):
                active = peak = 0
                ready = asyncio.Event()

                @tool
                async def probe(index: int) -> str:
                    """Offline concurrency probe."""
                    nonlocal active, peak
                    active += 1
                    peak = max(peak, active)
                    if active == 3:
                        ready.set()
                    await ready.wait()
                    await asyncio.sleep(0.01)
                    active -= 1
                    return str(index)

                self.agent = build_agent(self.model, [probe], checkpointer=self.saver)
                batch = [("probe", {"index": index}) for index in range(7)]
                if role == "main":
                    steps = [batch, "查询完成。"]
                else:
                    steps = [
                        ("task", {"subagent_type": "daily_planner", "description": "杭州三天两人"}),
                        batch,
                        ("write_file", {"file_path": "/daily-itinerary.md", "content": sample_plan()}),
                        "完整行程已保存。",
                        "行程已生成。",
                    ]
                await asyncio.wait_for(self.turn(
                    "并发查询", *steps,
                    config={"configurable": {"thread_id": f"parallel-{role}"}},
                ), timeout=5)
                self.assertEqual(peak, 3)
                self.assertEqual(active, 0)

    async def test_main_cannot_create_or_edit_itinerary(self):
        result = await self.turn(
            "检查行程写入权限",
            ("write_file", {"file_path": "/daily-itinerary.md", "content": "不应写入"}),
            "写入被拒绝。",
        )
        self.assertEqual(result["messages"][-2].status, "error")
        self.assertNotIn("/daily-itinerary.md", result.get("files", {}))
        await self.turn(
            "请子代理保存行程",
            ("task", {"subagent_type": "daily_planner", "description": "保存完整行程"}),
            ("write_file", {"file_path": "/daily-itinerary.md", "content": "原始行程"}),
            "已保存。", "行程已保存。",
        )
        result = await self.turn(
            "检查行程修改权限",
            ("edit_file", {"file_path": "/daily-itinerary.md", "old_string": "原始行程", "new_string": "不应修改"}),
            ("read_file", {"file_path": "/daily-itinerary.md"}),
            "修改被拒绝，仍可读取。",
        )
        self.assertEqual(result["messages"][-4].status, "error")
        self.assertIn("原始行程", result["messages"][-2].text)

    async def test_skills_remain_read_only(self):
        result = await self.turn(
            "检查skill写入边界",
            ("write_file", {"file_path": "/skills/flyai/SKILL.md", "content": "不应写入"}),
            "写入被拒绝。",
        )
        self.assertEqual(result["messages"][-2].status, "error")

    async def test_cli_rejects_non_search_command(self):
        with patch("agent.asyncio.create_subprocess_exec", new_callable=AsyncMock) as execute:
            result = await self.turn(
                "检查命令边界", ("flyai", {"command": "pay", "arguments": []}), "本次请求未执行。",
            )
            execute.assert_not_awaited()
            self.assertEqual(result["messages"][-2].status, "error")


class ItineraryModelTest(unittest.TestCase):
    def test_rejects_invalid_sequences_times_coordinates_and_costs(self):
        cases = [
            (lambda d: d["days"][0]["items"][0].update(cost=-1)),
            (lambda d: d["days"][0]["items"][0].update(end_time="10:00")),
            (lambda d: d["days"][0]["items"][1].update(start_time="11:30")),
            (lambda d: d["days"][0]["items"].pop(1)),
            (lambda d: d["days"][0]["items"][1].update(to_stop_id="missing")),
            (lambda d: d["days"][0]["items"][2].update(id="d1-sight")),
            (lambda d: d["days"][0]["items"][0]["coordinates"].update(latitude=100)),
            (lambda d: d["days"][1].update(date="2026-11-01")),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                data = sample_itinerary()
                mutate(data)
                with self.assertRaises(ValidationError):
                    Itinerary.model_validate(data)


if __name__ == "__main__":
    unittest.main()
