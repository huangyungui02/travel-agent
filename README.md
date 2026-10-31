# 旅行 Agent

单个旅行 Deep Agent，使用 `qwen3.8-flash`，在 LangGraph Studio 中完成需求澄清、交通酒店查询、预算规划、行程细化和反复修改。

- 百炼原生 `web_search`：查询攻略、开放时间、预约要求和最新公告。
- 高德 HTTP MCP：地点、路线和可查询的天气，工具由服务端动态发现。
- 飞猪官方 skill + CLI：自然语言查询交通、酒店和门票。
- 原生线程文件工具：用 Markdown 记录需求、预算、已确认安排和完整行程。
- 关闭通用子代理与 shell；由主 agent 规划全部天数，不代客下单或支付。

## 规划与记录

先了解需求，再查少量交通酒店候选、规划费用和每日骨架。用户自然确认交通、酒店和骨架后，主 agent 同轮继续详细规划，不需要固定口令或再次回复“继续”。用户可以反复修改，影响已确认安排时再商量取舍。

主 agent 在虚拟文件 `/travel-plan.md` 中用自然语言保存工作笔记，通过内置 `read_file`、`write_file`、`edit_file` 维护。笔记可以用标题、段落和表格，不要求 JSON 字段、阶段枚举、版本号或额外业务工具。

需求、预算与确认由主 agent 结合对话和笔记处理。它需要检查人数、房间数、晚数、已付与待付、共享费用和每日额度，标清报价、估算与未知费用，不重复计费。每轮保存后仍要向用户展示方案或问题。

笔记由 `StateBackend` 保存到线程状态，`langgraph dev` 管理检查点；同一线程可以继续，换线程不会自动读取另一趟旅行。虚拟文件不会直接写入项目目录。最终确认后，主 agent 把完整方案写入同一份笔记。当前没有跨线程方案库或前端路线导出。

主 agent 阅读飞猪官方 skill 与参数文档后，直接调用 `flyai(command, arguments)` 执行 CLI。
工具使用允许的搜索命令和独立参数数组，不经过额外模型转换。高德使用原生 MCP 接口。

## 安装与配置

需要 uv、Python 3.12+、Node.js 18+。已有 `.env` 时不要覆盖。

```sh
uv sync --locked
npm ci
cp .env.example .env
```

配置 `.env`：

```dotenv
TRAVEL_MODEL=qwen3.8-flash
DASHSCOPE_API_KEY=你的百炼密钥
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AMAP_MAPS_API_KEY=你的高德Key
FLYAI_API_KEY=你的飞猪密钥
```

百炼密钥与接口地域须一致。高德 MCP 必须能连接并提供工具；飞猪支持试用，正式使用可配置 Key。密钥仅保存在本地 `.env`，已加入 gitignore。

## 体验

```sh
uv run langgraph dev
```

打开终端输出的 Studio 链接，选择 `travel_agent`。启动时不连接远程工具，实际对话时才加载模型和高德 MCP。

可以这样连续对话：

1. `我想从上海去杭州玩三天，先帮我明确需求。`
2. `12月1日至3日，两位成人，一间房，两人共4000元，含往返、住宿、餐饮、当地交通、门票和机动费用。没有预订，想轻松游。`
3. `交通、酒店和每天的骨架都可以，就按这个继续细化。`
4. `第二天不要登山，其他安排尽量保留。`
5. `这份行程确定了，帮我保存完整方案。`

## 验证与执行边界

```sh
uv run python -m unittest -v
```

离线测试通过真实 SDK 的模拟 HTTP 响应，检查原生联网、飞猪 skill 和 CLI 帮助、Markdown 笔记的线程恢复与隔离，以及自然确认后的后续调用。不会进行付费查询。

模型运行使用 Responses API，关闭思考，输出上限 8192 token；单次模型请求超时 120 秒，飞猪 CLI 超时 90 秒。没有额外的模型／工具调用次数限制。`parallel_tool_calls=False` 用于减少同轮工具请求突发。

离线测试不能证明真实价格、库存、预订链接或景点开放状态正确，仍需在实际查询中核实。

## 文件

- `agent.py`：模型与工具组装、飞猪 CLI 查询、异步图工厂。
- `system_prompt.md`：完整旅行助手职责和自然语言笔记约定。
- `test_agent.py`：原生工具与线程笔记的离线集成检查。
- `plan.md`：当前精简方案与后续交付计划。

## 官方资料

- [百炼原生联网搜索](https://help.aliyun.com/zh/model-studio/web-search)
- [百炼结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
- [高德 MCP 快速接入](https://developer.amap.com/api/mcp-server/gettingstarted)
- [飞猪官方 skill](https://github.com/alibaba-flyai/flyai-skill)，本地来源见 `skills/SOURCE.md`。
- [Deep Agents 文档](https://docs.langchain.com/oss/python/deepagents/customization)
