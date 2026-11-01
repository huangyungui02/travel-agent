"""A stateful planner that submits a Markdown itinerary file."""

from pathlib import Path

from deepagents import FilesystemPermission, create_deep_agent

ROOT = Path(__file__).resolve().parent


def build_daily_planner(model, tools, backend):
    return create_deep_agent(
        name="daily_planner",
        model=model,
        tools=tools,
        backend=backend,
        skills=["/skills/"],
        permissions=[FilesystemPermission(paths=["/skills/**"], operations=["write"], mode="deny")],
        system_prompt=(ROOT / "daily_planner_prompt.md").read_text(encoding="utf-8"),
        checkpointer=True,
    ).with_config(max_concurrency=3)
