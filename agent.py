"""Travel Deep Agent, exposed as an async LangGraph graph factory."""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


FlyaiCommand = Literal[
    "keyword-search", "ai-search", "search-flight", "search-train",
    "search-hotel", "search-poi", "search-marriott-hotel", "search-marriott-package",
]


@tool
async def flyai(command: FlyaiCommand, arguments: list[str]) -> str:
    """调用飞猪官方 CLI。先阅读 /skills/flyai/SKILL.md 和对应 references。

    command 为允许的搜索命令；arguments 是独立参数数组，例如 ["--query", "杭州三日游"]。
    查看帮助可传 ["--help"]。按文档使用参数，不猜字段，不拼接 shell。
    仅支持搜索与帮助，不支持配置、下单或支付。
    """
    executable = ROOT / "node_modules" / ".bin" / "flyai"
    if not executable.exists():
        return "飞猪 CLI 尚未安装，请在 travel-agent 目录运行 npm ci。"
    process = await asyncio.create_subprocess_exec(
        str(executable), command, *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=ROOT,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
    except (TimeoutError, asyncio.CancelledError) as exc:
        process.kill()
        await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return "飞猪查询超时，请稍后重试；不能据此推断价格或库存。"
    if process.returncode:
        # stderr may contain URLs or credentials; keep those out of model context.
        return f"飞猪 CLI 查询失败（exit={process.returncode}）。请检查参数、网络与 API Key。"
    return stdout.decode("utf-8", errors="replace")


@tool
def current_date() -> str:
    """返回中国标准时间，帮助解析本周末、下个月等旅行日期。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def build_agent(model, tools, *, checkpointer=None):
    """Compile the harness; LangGraph dev supplies its own persistence."""
    system_prompt = (ROOT / "system_prompt.md").read_text(encoding="utf-8")
    register_harness_profile(
        "openai",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            excluded_tools=frozenset({"execute"}),
        ),
    )
    return create_deep_agent(
        name="travel_agent",
        model=model,
        tools=[
            current_date, flyai, *tools,
            {"type": "web_search"},
        ],
        backend=CompositeBackend(
            default=StateBackend(),
            routes={"/skills/": FilesystemBackend(root_dir=ROOT / "skills", virtual_mode=True)},
        ),
        skills=["/skills/"],
        permissions=[FilesystemPermission(paths=["/skills/**"], operations=["write"], mode="deny")],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )


async def graph(config):
    """Load remote MCP tools at run time, so server startup stays offline."""
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise ValueError("请在 .env 填写 DASHSCOPE_API_KEY（阿里云百炼 API Key）。")
    model = ChatOpenAI(
        model=os.getenv("TRAVEL_MODEL", "qwen3.8-flash"),
        api_key=key,
        base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        max_tokens=8192,
        use_responses_api=True,
        extra_body={"enable_thinking": False},
        # Avoid bursts of parallel requests against Amap's QPS quota.
        model_kwargs={"parallel_tool_calls": False},
        use_previous_response_id=False,
        timeout=120,
        max_retries=2,
    )
    key = os.getenv("AMAP_MAPS_API_KEY", "").strip()
    if not key:
        raise ValueError("请在 .env 填写 AMAP_MAPS_API_KEY，高德 MCP 是必需服务。")
    client = MultiServerMCPClient({
        "amap": {
            "transport": "http",
            "url": "https://mcp.amap.com/mcp?" + urlencode({"key": key}),
        },
    })
    try:
        tools = await client.get_tools()
    except Exception:
        raise RuntimeError("高德 MCP 连接失败，请检查 API Key、权限和网络。") from None
    if not tools:
        raise RuntimeError("高德 MCP 未返回可用工具，请检查服务状态与权限。")
    return build_agent(model, tools)
