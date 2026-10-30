# 旅行 Agent 最小版

单个旅行 Deep Agent，模型为 `qwen3.8-flash`，可在 LangGraph Studio 中对话。

- 百炼原生 `web_search`：搜索攻略和最新信息。
- 高德 HTTP MCP：地点、路线、天气，工具由服务端动态发现。
- 飞猪官方 skill：按需读取 `skills/flyai/` 和 references，再调用本地官方 CLI。
- 保留规划及线程文件工具，关闭默认通用子代理和 shell 执行；不进行下单或支付。
- 线程状态由 `langgraph dev` 管理；笔记不写入项目文件，skill 目录禁止 Agent 修改。

## 安装

需要 uv、Python 3.12+、Node.js 18+。Python 依赖使用 uv；飞猪官方 CLI 是 Node 包，需单独使用 npm 安装。

```sh
cd /Users/bruce/Projects/langgraph_test/travel-agent
uv sync --locked
npm ci
cp .env.example .env
```

本次已安装依赖并创建空密钥 `.env`，无需再次复制覆盖它。编辑 `.env`：

```dotenv
TRAVEL_MODEL=qwen3.8-flash
DASHSCOPE_API_KEY=你的百炼密钥
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AMAP_MAPS_API_KEY=你的高德Key
FLYAI_API_KEY=你的飞猪密钥
```

百炼 API Key 与接口地域须一致。上例为北京旧域名，官方说明仍可使用；推荐替换为业务空间专属地址 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。其他地域按官方文档修改。

飞猪允许无 Key 试用，正式使用建议填写 Key。高德 MCP 是必需服务，必须配置有效的 `AMAP_MAPS_API_KEY`；连接失败或没有可用工具时会报错。密钥只保存在本地 `.env`，已加入 gitignore。

## 启动

```sh
uv run langgraph dev
```

打开终端输出的 Studio 链接，选择 `travel_agent`。默认端口为 2024，若已占用 CLI 会选择其他端口，以实际输出为准。无需 Docker。

输入示例：

> 我从上海出发，下个月安排杭州三日游，两人预算 4000 元，喜欢美食和轻松步行。请查酒店、核实景点路线，并用网页搜索补充最新开放信息。

graph 使用异步工厂，在运行时加载模型与高德工具，因此空密钥也能启动开发服务器；实际对话前必须填写百炼和高德密钥。

## 原生联网接口

采用 **ChatOpenAI + 百炼 OpenAI 兼容 Responses API**，使用百炼密钥和 `qwen3.8-flash`。显式设置 `use_responses_api=True`，并通过 `extra_body={"enable_thinking": True}` 开启思考。

直接使用 `langchain_openai.ChatOpenAI` 和原生 `{"type": "web_search"}`，无需自定义模型类或工具兼容层。搜索在百炼服务端执行，本版只启用网页搜索。

支持模型流式输出，Studio 可随模型响应显示进度。设置 `parallel_tool_calls=False`，减少同轮并行调用造成的高德 QPS 限流；实际额度以高德账户为准。

## 验证与边界

```sh
uv run python -m unittest -v
```

离线集成检查通过真实 LangChain/OpenAI SDK 的模拟 HTTP 响应，验证模型请求带原生联网工具、隐藏 task 和 shell，读取官方 skill 与参数文档，并通过工具调用实际飞猪 CLI 的 `--help`。
这不等于已完成真实百炼、高德或飞猪查询；真实价格、库存、原生联网结果和 MCP 权限需填写密钥后验证。

核心入口是 `agent.py`，开发服务配置是 `langgraph.json`；Python 和 Node 依赖分别锁定在 `uv.lock`、`package-lock.json`。Deep Agents 的 profile 接口处于 beta，升级后需重新验证子代理配置与禁用 shell 的行为。

## 官方资料

- [百炼原生联网搜索](https://help.aliyun.com/zh/model-studio/web-search)
- [百炼 OpenAI 兼容接口与地域](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [高德 MCP 快速接入](https://developer.amap.com/api/mcp-server/gettingstarted)
- [飞猪官方 skill](https://github.com/alibaba-flyai/flyai-skill)，本地来源与提交见 `skills/SOURCE.md`。
- [Deep Agents 文档](https://docs.langchain.com/oss/python/deepagents/customization)
