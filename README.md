# 旅行 Agent

基于 Deep Agents 构建的旅行规划系统，使用 `qwen3.8-flash`，由主代理（`travel_agent`）与每日行程子代理（`daily_planner`）通过双 Markdown 文件协作，在 LangGraph Studio 中完成需求澄清、交通酒店查询、预算规划、行程细化和反复修改。

- 百炼原生 `web_search`：查询攻略、开放时间、预约要求和最新公告。
- 高德 HTTP MCP：地点、路线和可查询的天气，工具由服务端动态发现。
- 飞猪官方 skill + CLI：自然语言查询交通、酒店和门票。
- 双文件职责隔离：
  - `/travel-outline.md`（主代理读写，子代理只读）：记录需求、预算、已订项目、大交通与酒店候选及确认的每日行程骨架。
  - `/daily-itinerary.md`（子代理读写，主代理只读）：记录每日分时段详细行程、动线耗时、餐饮门票与分项预算。
- 关闭通用子代理与 shell；仅开放专用 `daily_planner` 子代理负责每日细节规划，不代客下单或支付。

## 规划与记录

先由主 agent 了解需求、查少量交通酒店候选、规划费用和每日骨架，并维护在虚拟文件 `/travel-outline.md` 中。用户自然确认交通、酒店和骨架后，主 agent 更新 `/travel-outline.md` 并同轮调用 `task(subagent_type="daily_planner")` 委派子代理读取 `/travel-outline.md`、生成 `/daily-itinerary.md`，再由主 agent 读取并呈现给用户。

用户可以反复修改：仅涉及每日具体安排时，主 agent 委派 `daily_planner` 局部修订 `/daily-itinerary.md`；涉及大交通、酒店、总预算或整体骨架变更时，主 agent 先更新 `/travel-outline.md` 并与用户确认，再调用子代理同步更新详细行程。

两份文件均由 `StateBackend` 保存到线程状态，`langgraph dev` 管理检查点；同一线程可以继续，换线程相互隔离，并由 `FilesystemPermission` 强制保证主／子代理各自的写入边界。

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

本地 dev 默认“关闭就停止”：正常关闭服务时取消未完成任务；重启时先取消上次遗留的 `pending`／`running` 任务，不自动续跑。对话历史、检查点和旅行笔记仍然保留，可以由用户发送新消息继续。强制终止进程时，清理会在下次启动执行。此策略由 `dev_server.py` 的 HTTP lifespan 钩子实现，使用本地 in-memory runtime 的取消接口；升级服务端依赖后需验证生命周期顺序。若要恢复框架默认的重启续跑行为，移除 `langgraph.json` 中的 `http.app` 配置。关闭浏览器页面不等于关闭服务。

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

- `agent.py`：主／子代理与工具组装、双文件权限配置、飞猪 CLI 查询、异步图工厂。
- `dev_server.py`：本地 dev 启停时取消未完成任务，保留对话和笔记。
- `system_prompt.md`：主代理旅行助手职责与 `/travel-outline.md` 维护约定。
- `daily_planner_prompt.md`：`daily_planner` 子代理职责与 `/daily-itinerary.md` 编排约定。
- `test_agent.py`：原生工具、双文件权限、线程隔离与子代理委派的离线集成检查。

## 官方资料

- [百炼原生联网搜索](https://help.aliyun.com/zh/model-studio/web-search)
- [百炼结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
- [高德 MCP 快速接入](https://developer.amap.com/api/mcp-server/gettingstarted)
- [飞猪官方 skill](https://github.com/alibaba-flyai/flyai-skill)，本地来源见 `skills/SOURCE.md`。
- [Deep Agents 文档](https://docs.langchain.com/oss/python/deepagents/customization)
