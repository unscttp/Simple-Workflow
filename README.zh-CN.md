[English](README.md) | **简体中文**

# Simple Workflow

一个带网页聊天界面的轻量级 AI 助手。它可以搜索网络、分析 JSON 数据，并把报告（Markdown、Word、PDF）
写入你批准的目录。后端基于 [NLG 工作流框架](https://github.com/unscttp/nlg) 构建；本仓库同时是该框架的
测试场：它在真实应用中使用框架，也是发现框架缺陷的地方。

## 目录

- [工作方式](#工作方式)
- [快速开始](#快速开始)
- [配置](#配置)
- [模型服务](#模型服务)
- [权限与审批](#权限与审批)
- [工具](#工具)
- [技能：综述生成](#技能综述生成)
- [工作流图视图](#工作流图视图)
- [长期知识](#长期知识)
- [HTTP API](#http-api)
- [数据与隐私](#数据与隐私)
- [开发](#开发)
- [测试框架](#测试框架)
- [与上一版本的差异](#与上一版本的差异)
- [限制](#限制)

## 工作方式

```
frontend (Next.js) ──HTTP──> backend (FastAPI)
                               └─ AgentService
                                   ├─ 每条消息对应一个 NLG 任务（"chat-turn" 工作流）
                                   ├─ 模型适配器 ── DeepSeek / Claude / GPT / Gemini
                                   ├─ 工具：web、data、text、reports
                                   ├─ 技能：survey-creator（Agent Skills 包）
                                   ├─ 生成图：经审核的子工作流（graph.create）
                                   ├─ allowance：risk-low / risk-medium / risk-high
                                   ├─ 知识库（经审核，跨对话共享）
                                   └─ 运行中任务登记 ── 工作流图视图
```

- 你发送的每条消息都会启动一个 `chat-turn` 工作流任务（`backend/config/workflow.json`）：
  只有一个 LLM 节点，它可以循环调用工具，最后给出回答。每段最多 30 次模型决策（每次审批后重新计数），并会告诉模型这个上限；
  用完时不会直接失败，而是再给它一个不带工具的回合，根据已找到的信息作答（NLG 的 `react.on_step_limit: "answer"`）。
- 模型通过原生 tool calling 调用。适配器（`backend/app/model.py`）把框架的提示词转换为与服务商无关的一轮请求
  （聊天消息、工具定义、本轮之前的工具调用及结果）；`backend/app/providers/` 中的各个服务商模块按各自 API 的格式发送，
  再把回复转换为工具调用或最终回答。
- 每条消息都可以选择服务商和模型，因此同一段对话可以换一个模型继续。
- 当某个工具需要你批准时，本轮会暂停。界面会展示将要执行的操作；你允许后继续执行，拒绝则本轮结束。
- 每一轮结束后，连同完整日志保存在 `SW_DATA_DIR` 下。

## 快速开始

连同框架子模块一起克隆：

```bash
git clone --recurse-submodules https://github.com/unscttp/Simple-Workflow.git
```

已有克隆可以用 `git submodule update --init` 获取子模块。

### Docker

```bash
docker compose up --build
```

打开 `http://localhost:3000`，选择模型服务并填入对应的 API Key 即可开始对话。端口通过 `FRONTEND_PORT`（默认 3000）
和 `BACKEND_PORT`（默认 8000）设置。数据（包括报告目录）保存在 `sw-data` 数据卷中；在 Docker 里，
报告目录必须位于 `/data/reports` 之内。

### 本地开发

需要 Python 3.10+ 和 Node.js 18+。

```bash
python -m venv .venv && source .venv/bin/activate
```

```bash
pip install -r backend/requirements-dev.txt -e third_party/nlg
```

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

在另一个终端中：

```bash
cd frontend && npm install && npm run dev
```

除非设置了 `NEXT_PUBLIC_API_BASE_URL`，前端会请求 `http://localhost:8000`。
交互式 API 文档位于 `http://localhost:8000/docs`。

## 配置

后端通过环境变量配置：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `SW_DATA_DIR` | `./data` | 会话、已保存的对话轮次和知识库 |
| `SW_REPORTS_ROOT` | 未设置 | 设置后，只能批准该目录之内的报告目录 |
| `SW_DEFAULT_PROVIDER` | `deepseek` | 请求未指定服务商时使用的服务商：`deepseek`、`anthropic`、`openai` 或 `gemini` |
| `SW_<PROVIDER>_MODEL` | 见[模型服务](#模型服务) | 请求未指定模型时使用的模型，例如 `SW_ANTHROPIC_MODEL` |
| `SW_<PROVIDER>_BASE_URL` | 服务商官方 API | 该服务商 API 的其他地址，例如代理 |
| `SW_DEFAULT_RISK_LEVEL` | `medium` | 新会话的风险级别 |
| `SW_KNOWLEDGE_CONSOLIDATION` | `true` | 每轮结束后询问模型哪些内容值得记住（多一次模型调用） |
| `SW_HISTORY_MESSAGES` | `20` | 每轮随请求发送的历史消息条数 |
| `SW_CORS_ORIGINS` | `http://localhost:3000` | 允许调用 API 的来源，逗号分隔 |
| `SW_MODEL_RETRIES` | `3` | 模型请求超时、连接中断或服务繁忙时的自动重试次数（0 表示关闭；见[模型服务](#模型服务)） |
| `SW_MODEL_TIMEOUT_SECONDS` | `300` | 等待模型回答的最长时间（建立连接的超时为 15 秒） |
| `SW_SEARCH_PROVIDER` | `auto` | 网络搜索后端：`tavily`、`brave`、`searxng`、`duckduckgo` 或 `auto`（见[工具](#工具)） |
| `SW_TAVILY_API_KEY` / `SW_BRAVE_API_KEY` | 未设置 | Tavily 或 Brave Search API 的密钥 |
| `SW_SEARXNG_URL` | 未设置 | 启用了 JSON 格式的 SearXNG 实例地址 |
| `SW_OPENALEX_API_KEY` | 未设置 | `scholar.search` 可选的 OpenAlex 密钥（不设置也能使用） |

本应用使用的框架设置（提示词大小预算、知识审核、各种上限）位于 `backend/config/memory_settings.json`，
各项含义见 NLG 的 README。

## 模型服务

| 服务商 | `provider` | API | 默认模型 |
| --- | --- | --- | --- |
| DeepSeek | `deepseek` | Chat Completions（`openai` SDK） | `deepseek-chat` |
| Claude | `anthropic` | Messages（`anthropic` SDK） | `claude-opus-5-5` |
| GPT | `openai` | Responses（`openai` SDK） | `gpt-5.5` |
| Gemini | `gemini` | Google 的 OpenAI 兼容 Chat Completions 接口（`openai` SDK） | `gemini-3.8-flash` |

界面在浏览器中为每个服务商分别保存一个 API Key 和一个模型。“读取可用模型”会向服务商查询该 Key 可用的模型
（`POST /api/providers/{provider}/models`），也可以直接输入任意模型 ID。使用前请向服务商确认默认模型 ID，
必要时用 `SW_<PROVIDER>_MODEL` 覆盖。

同一轮内工具调用之后，各服务商如何继续：

- **Claude**：每次工具调用时助手的完整内容（包括 thinking 块）原样发回，模型在多次工具调用之间保留推理过程。
  thinking 使用模型的默认设置（当前模型为自适应）。在支持服务端拒绝回退的模型上（`claude-fable-5-2`、
  `claude-fable-5-1`、`claude-opus-5-5`、`claude-opus-5`、`claude-sonnet-5-5`），请求会启用该功能
  （`fallbacks: "default"`）：如果模型拒绝请求，API 可能在同一次调用中改由回退模型回答。仍被拒绝的请求会使本轮失败，
  并给出拒绝类别。提示词前缀自动缓存；知识整理使用结构化输出。
- **GPT**：请求不在服务端保存（`store: false`）；每一步的加密推理条目和函数调用会在下一次请求中发回。
- **Gemini**：每个工具调用都带有 thought signature，本轮后续请求必须带上它；它随该步骤保存并发回。
  Gemini 的工具只接受 JSON Schema 的一个子集，否则会拒绝整个请求（HTTP 400），因此发给它的工具 schema 会先转换
  （`backend/app/providers/gemini_schema.py`）：内联 `$ref`，类型列表改为 `nullable`，为 enum 补上类型，
  删除 `additionalProperties` 等关键字，没有参数的工具不发送参数。工具运行前 NLG 仍会按原始 schema 校验参数。
- **DeepSeek**：思考模型的 `reasoning_content` 会随同一轮的工具调用发回。请求的输出上限设为 8192 个 token
  （API 默认 4096，不足以在一次工具调用中写出长报告）。

这些状态随每一步保存在 NLG 日志中（框架 ReAct 历史的 `model_state`），从不以文本形式展示给模型。
推理状态与生成它的模型以及之前的请求内容绑定，因此只有在本轮的模型、系统提示词和工具都没有变化、
且提示词中没有省略步骤时才会回放。否则（例如在批准工具调用时换了模型）只发送普通的工具调用和结果。

每个服务商每一步只被要求调用一个工具（NLG 每次决策执行一个动作）。

**自动重连。** 超时、连接中断以及 HTTP 408、409、425、429、5xx 和 529 都会被标记为暂时性错误，由 NLG 重试（`model_retry_attempts`）：
最多 `SW_MODEL_RETRIES` 次，依次等待 2、4、8……秒（最长 30 秒）。界面中的“自动重连”选项可以按请求开关（请求体中的 `auto_retry`）；
除非 `SW_MODEL_RETRIES` 为 0，否则默认开启。SDK 自身的隐式重试已关闭，因此每次重试都会记入本轮日志，并计入本轮统计。
其他错误（密钥无效、请求被拒绝等）会立即报告。等待回答的超时为 `SW_MODEL_TIMEOUT_SECONDS`（300 秒），足够写完一整份报告。

## 权限与审批

只有你能改变助手可以做什么。模型没有任何修改权限或批准自身请求的工具。

**风险级别。** 每个会话都有一个风险级别，在界面上设置。它决定每次工具调用使用的 allowance：

| 级别 | 允许 |
| --- | --- |
| `low` | 搜索、分析、读取报告和知识 |
| `medium`（默认） | 另外可以保存和编辑报告、提出知识变更 |
| `high` | 另外可以删除报告 |

超出当前级别的工具不会提供给模型；即使模型仍然调用也会被拒绝。在某轮等待审批期间降低级别，
会在该轮恢复执行时生效。

**报告目录。** 报告工具只能在你为本会话批准的目录中工作。助手通过 `workspace.request_access`
（目录和用途）申请，本轮会等待你的决定。系统目录总是被拒绝；设置了 `SW_REPORTS_ROOT` 时，
其外的目录也会被拒绝。你可以随时撤销目录授权，报告工具会立即停止工作。

**删除**报告总是需要你批准，即使风险级别是 `high`。

## 工具

| 工具（模型看到的名称） | 风险 | 审批 | 用途 |
| --- | --- | --- | --- |
| `web.search`（`web_search`） | low | – | 网络搜索：标题、摘要、链接（后端见下文） |
| `scholar.search`（`scholar_search`） | low | – | 来自 OpenAlex 的学术论文：作者、年份、期刊/会议、DOI、引用数、摘要 |
| `data.analyze_trend`（`data_analyze_trend`） | low | – | JSON 数值的数量、均值、中位数、最小值、最大值和变化 |
| `text.keywords`（`text_keywords`） | low | – | 文本中出现最多的词 |
| `text.redact`（`text_redact`） | low | – | 遮盖邮箱地址和电话号码 |
| `workspace.request_access`（`workspace_request_access`） | low | 每次调用 | 请你批准报告目录 |
| `reports.list` / `reports.read` | low | – | 列出报告；读取 `.md` 和 `.docx` |
| `reports.save` | medium | – | 保存新的 `md`、`docx` 或 `pdf` 报告；不会覆盖已有文件 |
| `reports.edit` | medium | – | 对 `.md`/`.docx` 报告追加、重写或替换某一节；会保留备份 |
| `reports.delete` | high | 每次调用 | 删除一份报告 |
| `knowledge.list` / `read` / `search` | low | – | 读取经审核的知识 |
| `knowledge.propose` | medium | 审核队列 | 提出知识变更（见下文） |
| `skill.survey-creator.invoke` / `read` / `finish` | low | – | 打开综述技能、读取其中的文件、关闭技能 |
| `skill.survey-creator.script` | high | 每次调用 | 运行技能中的 `scripts/check_survey.py` |
| `skill.survey-creator.run_graph` | high | 每个图 | 把技能中保存的图作为子任务运行 |
| `graph.create` | high | 每个图 | 把经审核的工作流图作为子任务运行 |

**网络搜索后端。** `SW_SEARCH_PROVIDER=auto` 会依次使用已配置的 Tavily（`SW_TAVILY_API_KEY`）、Brave（`SW_BRAVE_API_KEY`）
和 SearXNG（`SW_SEARXNG_URL`），都没有配置时通过 `ddgs` 库使用 DuckDuckGo；后者不需要密钥，但抓取的是公开网页，可能被限流。
想要稳定的结果，请配置一个搜索 API。`GET /health` 会显示正在使用的后端，每条结果都注明 `source`；结果为空时会附带提示，
让模型换关键词或换工具。（此前应用使用已废弃的 `duckduckgo_search` 包，其默认后端抓取 Bing 网页，返回的结果与查询无关或为空。）

**学术搜索。** `scholar.search` 查询开放的论文索引 OpenAlex，不需要密钥。支持年份范围，可以按相关度、引用数或时间排序，
使用英文关键词效果最好。综述技能用它收集文献。

工具运行前会按其 JSON schema 校验参数；错误会返回给模型，以便它修正调用。文件名只能是不含目录的普通名称。
新增工具请参考 `skills/adding-tools/SKILL.md`。

## 技能：综述生成

`backend/skills/survey-creator/` 是一个 [Agent Skills](https://agentskills.io/specification) 技能包，
用于撰写学术综述（文献综述论文）：确定综述范围，用网络搜索收集并筛选文献，建立分类体系和对比表，
最后把带引用的 Markdown 综述保存到已批准的目录。NLG 通过 `register_skill` 注册它，把它变成普通工具，
和其他工具一样受会话风险级别约束。

| 文件 | 内容 |
| --- | --- |
| `SKILL.md` | `invoke` 返回的说明：基本规则（绝不编造文献）、八个步骤、工作流图选项 |
| `templates/outline.md` | 综述的章节规划 |
| `references/structure.md` | 如何建立分类体系、对比表以及挑战与展望 |
| `references/citations.md` | 数字引用格式、类 APA 与 GB/T 7714-2015 参考文献格式 |
| `graphs/survey-pipeline.json` / `.md` | 标准流水线图及其使用场景，格式与 NLG 把图保存进技能时相同 |
| `scripts/check_survey.py` | 检查已保存的综述：章节、引用编号、缺少链接的参考文献、文献新近程度 |

在对话中提出要求即可，例如“写一篇关于检索增强生成的综述”。具体过程取决于风险级别：

- **low / medium**：助手打开技能，读取需要的文件，自己完成各个步骤。保存报告需要 medium。
- **high**：额外提供三个工具。`skill_survey-creator_run_graph` 运行技能中保存的图，`graph_create` 运行修改后的图，两者都作为生成图运行：运行前你会在审批卡片中看到各阶段及其权限，
  运行时工作流图视图会跟踪子任务。研究阶段使用 `survey-research` allowance（只能搜索和读取技能），
  撰写阶段使用 `survey-writer`（另可保存和编辑报告）。之后 `skill_survey-creator_script` 会对保存的文件运行检查脚本，
  同样需要你批准。各阶段有各自的步数上限（scope 6、collect 24、organise 10、write 14），同样有最后一个回合；
  它们能拿到已批准的目录（包括本轮中刚刚批准的目录），撰写阶段会分段保存较长的综述。

### 技能记住的图

技能会保留运行时用过的图，下次写综述时可以从一个已经成功过的图开始：

1. 打开技能时会列出其中保存的图，按时间从新到旧，附带标题、适用场景和各个阶段。`survey-pipeline` 随技能提供。
2. 助手用 `skill_survey-creator_run_graph` 按 id 重新运行最合适的图，不需要复制整个图。NLG 通过技能的 `SkillGraphInterface`
   钩子运行它，审核和权限检查与其他生成图相同。审批卡片会注明复用的是哪个保存的图。
3. 当请求需要不同的流程时（例如增加文献来源，或增加针对中文文献的阶段），助手会修改最接近的图，并请求连同使用场景一起保存。
   只有修改后的图成功运行之后才会询问你；拒绝保存不会影响本轮结果。

NLG 还会在不经过模型的情况下，把每个保存的图的运行环境和历史记录在 `graphs/<id>.meta.json` 中：
保存时以及最近一次成功运行时的环境（节点类型、各阶段使用的工具和 allowance，以及本应用作为宿主事实声明的模型）、运行次数，
以及上一次运行的结果和错误。打开技能时，每个图都会附带状态（`same`、`changed`、`incompatible` 或 `unknown`）、具体变化和提示，
例如该图上次成功运行时用的是另一个模型。技能说明要求助手遵循提示：环境相同的图可以复用，不兼容的图要修改后再用，
环境有变化的图要自行判断。

保存的图写入已安装技能的 `graphs/<id>.json` 和 `.md`，位于 `SW_DATA_DIR/skills/survey-creator/` 下，
因此升级后仍然保留，Docker 数据卷也会保存它们。启动时会用随应用提供的技能文件覆盖该目录，但不会动保存的图。
删除其中的文件即可让技能忘记某个图。

这里用到的 NLG 功能：技能调用、限定在技能包内的文件读取与路径检查、子任务中的技能调用、按哈希固定并需人工批准的脚本、
带 allowance 子集检查和审核的生成图、子任务审批转交给父任务、把图保存到技能并通过 `SkillGraphInterface` 钩子再次运行，以及日志和审计记录中的技能活动。

要添加其他技能，把技能包放到 `backend/skills/<名称>/`，在 `backend/app/tools/__init__.py` 的 `SKILLS` 中登记它及可运行的脚本，
并把对应的 `tool:skill.<名称>.*` 动作加入三个风险 allowance。

## 工作流图视图

**Workflow Graph** 面板显示当前一轮运行所在的工作流图以及进行到哪一步。每个节点显示状态（未开始、运行中、等待、完成、失败），
运行或等待时还会显示正在做什么：等待模型决策、调用工具、运行生成的子图或等待你确认。已完成的节点显示模型决策次数和工具调用次数。
生成图显示在创建它的节点下方，附带说明、来源技能以及自己的节点状态。

请求进行期间，界面每秒轮询一次 `GET /api/sessions/{id}/graph`。后端在每个引擎上注册了 NLG 运行观察者，
因此能在任务保存之前看到本轮任务以及生成图子任务的实时状态。没有正在运行的一轮时，视图显示等待审批的一轮，否则显示最近一轮。

## 长期知识

各次对话彼此独立，但有用的结论可以保留下来。每轮结束后，模型可以提出简短的知识条目（例如搜索确认的事实）。
提案会在 **Knowledge** 面板中等待你采纳或拒绝；任何内容都不会自行进入知识库。使用过外部内容
（网络搜索、报告文件）的轮次提出的提案会标记为“含外部来源”。采纳后的知识会以索引形式提供给之后的对话，
模型可以从中读取；任何变更都可以通过 API 撤销。

## HTTP API

| 方法和路径 | 用途 |
| --- | --- |
| `GET /health` | 健康检查 |
| `GET /api/providers` | 服务商、各自的默认模型以及默认服务商 |
| `POST /api/providers/{provider}/models` | 该 Key 可用的模型 ID `{api_key}` |
| `POST /api/sessions` | 创建会话 |
| `GET /api/sessions/{id}` | 会话：风险级别、目录、消息、待审批请求 |
| `GET /api/sessions/{id}/graph` | 正在运行、等待审批或最近一轮的工作流图（`?task_id=` 指定本会话的其他轮次） |
| `PUT /api/sessions/{id}/risk-level` | 设置风险级别 `{risk_level}` |
| `DELETE /api/sessions/{id}/workspace` | 撤销报告目录 |
| `POST /api/sessions/{id}/messages` | 运行一轮 `{api_key, message, provider?, model?, auto_retry?}` |
| `POST /api/sessions/{id}/approvals/{request_id}` | 作出决定 `{api_key, approved, comment?, provider?, model?}` 并继续本轮 |
| `GET /api/knowledge` | 知识页面和待审核提案 |
| `GET /api/knowledge/pages/{scope}/{slug}` | 读取一个页面 |
| `POST /api/knowledge/proposals/{id}` | 采纳或拒绝 `{approved, comment?}` |
| `POST /api/knowledge/changes/{id}/revert` | 撤销一次已生效的变更 |

每轮返回 `status`（`completed`、`waiting` 或 `failed`）、`answer`、`tool_logs`、`audit_entries`、
`pending_approvals`、`stats`（使用的模型、模型决策次数、token 数和工具调用次数）以及更新后的 `session`。
助手消息会记录生成它的 `provider/model`。某轮等待决定期间，新消息会以 HTTP 409 拒绝。

## 数据与隐私

- API Key 保存在浏览器本地存储中，随每次请求发送，只发给后端，且只发送所选服务商的 Key。后端只在该请求中使用它，不会写入会话、日志或记录，
  服务商拒绝请求时会显示其原因（例如“HTTP 400: Please pass a valid API key”），其中任何看起来像密钥的内容都会被删除。
- `SW_DATA_DIR` 保存会话（消息、风险级别、已批准目录）、每轮的日志（工具参数和结果）以及知识库。
- 没有登录功能。任何能访问 API 的人都能使用和审核所有会话；请在本地运行，或部署在你自己的认证之后。

## 开发

```bash
cd backend && python -m unittest discover -s tests -t .
```

```bash
ruff check . && ruff format --check .
```

```bash
cd frontend && npx tsc --noEmit && npm run build
```

后端测试可以离线运行：由脚本化的模型代替各服务商，由桩函数代替网络搜索；各服务商的请求格式通过返回 SDK 自带响应类型的假客户端测试。

| 路径 | 内容 |
| --- | --- |
| `backend/app/main.py` | FastAPI 路由 |
| `backend/app/agent.py` | 以 NLG 任务运行对话轮次：引擎配置、执行、审批 |
| `backend/app/model.py` | NLG 调用的适配器，与服务商无关 |
| `backend/app/providers/` | 每种模型 API 一个模块：Chat Completions、Responses、Messages |
| `backend/app/tools/` | 工具定义和处理函数 |
| `backend/app/journal.py` | 从每轮日志生成工具记录、审计记录和审批请求 |
| `backend/app/graph_view.py`、`backend/app/live.py` | 工作流图视图和运行中任务的登记 |
| `backend/skills/` | 助手可以使用的 Agent Skills（survey-creator） |
| `backend/app/sessions.py` | 会话和对话轮次的存储 |
| `backend/config/` | 工作流、allowance 和框架设置 |
| `backend/prompts/system_prompt.md` | 助手的指令（中文） |
| `frontend/` | Next.js 界面 |
| `third_party/nlg/` | NLG 框架（git 子模块） |
| `skills/adding-tools/` | 新增工具指南 |
| `easter-eggs/`、`frontend/public/snake/` | 后端无法访问时提供的贪吃蛇小游戏 |

## 测试框架

本仓库在 `third_party/nlg` 中固定了 NLG 的某个提交。要试用更新的框架版本：

```bash
git -C third_party/nlg fetch origin && git -C third_party/nlg checkout <commit>
```

然后运行后端测试，并提交新的子模块提交。这里用到的框架功能有：带 ReAct 的 LLM 节点、allowance 与风险上限、
按 schema 校验的工具、可暂停并在保存后恢复任务的人工审批、任务日志、任务持久化、受限大小的提示词，
带审核的长期知识、Agent Skills、生成图以及运行观察者。

迁移过程中发现并已在框架中修复的问题：

- 工具参数没有按 `input_schema` 校验；
- LLM 节点无法在没有主动记忆写入器的情况下运行（每次工具调用都多一次模型调用）；
- 框架无法通过 `pip install -e` 安装；
- 知识整理看不到待审核的提案，导致相邻两轮重复提出同一个页面；
- ReAct 动作无法把服务商状态（Gemini 的 thought signature、Claude 的 thinking 块、GPT 的推理条目）带到下一次决策，
  导致 Gemini 在第二次工具调用时失败，其他模型在工具调用之间丢失推理过程。现在动作带有不透明的 `model_state`；
- 在 `graph.create` 调用返回之前，宿主看不到生成图的子任务，因此既无法显示其进度，也无法判断子任务属于哪个会话。
  现在 `engine.add_run_observer` 会报告每个节点 execution，包括子任务中的；
- 保存到技能中的图之后无法被找到或再次运行：打开技能时不会提到它，也没有按 id 运行的方式。而且保存审核发生在图运行之前，
  拒绝保存会让节点失败。现在 `skill.<名称>.invoke` 会列出保存的图，`skill.<名称>.run_graph` 通过已实现的 `SkillGraphInterface` 运行它们，
  只有成功运行后才会提议保存，拒绝保存也不会丢弃结果。

在本应用中发现并处理、未改动框架的问题：

- 生成图的子任务只带有父任务的 id，不带宿主的元数据（这里是会话 id）。报告工具通过运行中任务的登记查找子任务所属的会话，
  而不信任任务数据，因为生成图可以写入任务数据。
- ReAct 历史会把每个字符串截断到 `prompt_max_value_chars`，技能说明、用 `skill.<名称>.read` 读取的文件（以及从技能复制的图）
  也会被截断。`backend/config/memory_settings.json` 把它提高到 16000，并把 `prompt_budget_chars` 提高到 48000，
  使完整的技能文件能到达模型。如果框架支持按工具设置上限，就不必让每个提示词都变大。

### 比较不同模型

每轮都会报告由哪些模型运行、用了多少次决策和工具调用、消耗了多少 token；界面在输入框下方显示这些信息，
每个保存的轮次也会把它们记录在 `task.metadata["models"]` 和日志中。要比较模型，可以在新会话中用不同服务商发送同一个任务，
或者在对话中途切换服务商，观察另一个模型如何在相同的历史上继续。

## 与上一版本的差异

- 模型不再能自己批准目录访问或提高自己的风险级别：这两项以前都是模型可调用的工具。
  现在目录访问是真正的人工审批，风险级别是会话设置。
- 十个功能重叠的文件工具合并为五个 `reports.*` 工具。保存不再覆盖已有文件；编辑时显式指定章节；
  PDF 导出现在能正确显示中文。
- 移除了工具注册表、基于 AST 检查的工具脚手架脚本及其 skill。工具改为在代码中连同 schema 一起定义
  （见 `skills/adding-tools`）。
- 新增：界面上的工具审批、服务端会话持久化、基于框架日志的审计记录、带审核的长期知识。
- API：`POST /api/agent` 被会话相关接口取代。客户端不再发送历史记录和权限状态，两者都由服务端维护。

## 限制

- 单用户，没有认证（见[数据与隐私](#数据与隐私)）。
- 只支持一个后端进程：同一会话的轮次在进程内串行执行，不跨进程协调。
- 助手每一步只调用一个工具。
- 没有配置搜索 API 密钥时，网络搜索使用 DuckDuckGo 的公开网页，可能被限流。各搜索后端和 OpenAlex 的测试使用模拟响应，
  尚未在本仓库的测试环境中对真实服务运行过。
- Gemini schema 转换依据 Gemini 文档中的 schema 子集实现，并有离线测试覆盖，但尚未在真实 API 上运行过。
- 工作流图视图在请求进行期间轮询；有多个后端进程时，只能看到由应答进程运行的轮次。
- 使用脚本化模型和假的服务商客户端离线测试；接入真实模型后的表现取决于模型是否遵循 `system_prompt.md` 中的指令。
