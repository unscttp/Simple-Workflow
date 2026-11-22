**English** | [简体中文](README.zh-CN.md)

# Simple Workflow

A lightweight AI assistant with a web chat interface. It searches the web,
analyses JSON data and writes reports (Markdown, Word, PDF) into a folder you
approve. The backend is built on the [NLG workflow framework](https://github.com/unscttp/nlg),
and this repository doubles as its test bed: it exercises the framework in a
real application and is where framework gaps are found.

## Contents

- [How it works](#how-it-works)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Model providers](#model-providers)
- [Permissions and approvals](#permissions-and-approvals)
- [Tools](#tools)
- [Skills: survey creator](#skills-survey-creator)
- [Workflow graph view](#workflow-graph-view)
- [Long-term knowledge](#long-term-knowledge)
- [HTTP API](#http-api)
- [Data and privacy](#data-and-privacy)
- [Development](#development)
- [Testing the framework](#testing-the-framework)
- [Changes from the previous version](#changes-from-the-previous-version)
- [Limitations](#limitations)

## How it works

```
frontend (Next.js) ──HTTP──> backend (FastAPI)
                               └─ AgentService
                                   ├─ one NLG task per chat message ("chat-turn" workflow)
                                   ├─ model adapter ── DeepSeek / Claude / GPT / Gemini
                                   ├─ tools: web, data, text, reports
                                   ├─ skills: survey-creator (Agent Skills package)
                                   ├─ generated graphs: reviewed child workflows (graph.create)
                                   ├─ allowances: risk-low / risk-medium / risk-high
                                   ├─ knowledge store (reviewed, shared across chats)
                                   └─ live task registry ── workflow graph view
```

- Every message you send starts one task of the `chat-turn` workflow
  (`backend/config/workflow.json`): a single LLM node that may call tools in a
  loop and then answers.
- The model is called with native tool calling. The adapter
  (`backend/app/model.py`) turns the framework's prompt into a
  provider-neutral turn (chat messages, tool definitions, earlier tool calls
  and results); each provider in `backend/app/providers/` sends it in its own
  API's format and turns the reply into a tool call or the answer.
- You choose the provider and model for every message, so the same
  conversation can be continued on another model.
- When a tool needs your approval, the turn pauses. The interface shows what
  would happen; the turn continues after you allow it, or ends if you reject it.
- Each finished turn is saved with its full journal under `SW_DATA_DIR`.

## Getting started

Clone with the framework submodule:

```bash
git clone --recurse-submodules https://github.com/unscttp/Simple-Workflow.git
```

In an existing clone, fetch it with `git submodule update --init`.

### Docker

```bash
docker compose up --build
```

Open `http://localhost:3000`, choose a model provider, paste its API key and
start chatting.
Ports are set with `FRONTEND_PORT` (default 3000) and `BACKEND_PORT` (default
8000). Data, including report folders, is kept in the `sw-data` volume; in
Docker, report folders must be inside `/data/reports`.

### Local development

Requirements: Python 3.10+, Node.js 18+.

```bash
python -m venv .venv && source .venv/bin/activate
```

```bash
pip install -r backend/requirements-dev.txt -e third_party/nlg
```

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd frontend && npm install && npm run dev
```

The frontend calls `http://localhost:8000` unless `NEXT_PUBLIC_API_BASE_URL` is set.
Interactive API documentation is at `http://localhost:8000/docs`.

## Configuration

Backend settings are environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SW_DATA_DIR` | `./data` | Sessions, saved turns and the knowledge base |
| `SW_REPORTS_ROOT` | unset | If set, report folders can only be approved inside it |
| `SW_DEFAULT_PROVIDER` | `deepseek` | Provider used when a request names none: `deepseek`, `anthropic`, `openai` or `gemini` |
| `SW_<PROVIDER>_MODEL` | see [Model providers](#model-providers) | Model used when a request names none, e.g. `SW_ANTHROPIC_MODEL` |
| `SW_<PROVIDER>_BASE_URL` | the provider's API | Another endpoint for that provider's API, e.g. a proxy |
| `SW_DEFAULT_RISK_LEVEL` | `medium` | Risk level of new sessions |
| `SW_KNOWLEDGE_CONSOLIDATION` | `true` | After each turn, ask the model what is worth remembering (one extra model call) |
| `SW_HISTORY_MESSAGES` | `20` | Earlier chat messages sent with each turn |
| `SW_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated origins allowed to call the API |

Framework settings for this app (prompt size budgets, knowledge review, limits)
are in `backend/config/memory_settings.json`; see the NLG README for each key.

## Model providers

| Provider | `provider` | API | Default model |
| --- | --- | --- | --- |
| DeepSeek | `deepseek` | Chat Completions (`openai` SDK) | `deepseek-chat` |
| Claude | `anthropic` | Messages (`anthropic` SDK) | `claude-opus-5-5` |
| GPT | `openai` | Responses (`openai` SDK) | `gpt-5.5` |
| Gemini | `gemini` | Google's OpenAI-compatible Chat Completions endpoint (`openai` SDK) | `gemini-3.8-flash` |

The interface keeps one API key and one model per provider in the browser.
"Load available models" asks the provider which models the key can use
(`POST /api/providers/{provider}/models`); any model ID can also be typed in.
Check the default model IDs against your provider before relying on them, and
override them with `SW_<PROVIDER>_MODEL` if needed.

How each provider continues after a tool call within one turn:

- **Claude**: the full assistant content of each tool call, thinking blocks
  included, is sent back unchanged, so the model keeps its reasoning between
  tool calls. Thinking runs with the model's defaults (adaptive on current
  models). On models that support server-side refusal fallbacks
  (`claude-fable-5-2`, `claude-fable-5-1`, `claude-opus-5-5`, `claude-opus-5`,
  `claude-sonnet-5-5`), requests enable them (`fallbacks: "default"`): if the
  model declines a request, the API may answer it with a fallback model within
  the same call. A request that is still declined fails the turn with the
  refusal category. The prompt prefix is cached automatically; knowledge
  consolidation uses structured output.
- **GPT**: requests are stateless (`store: false`); the encrypted reasoning
  items and the function call of each step are sent back on the next request.
- **Gemini**: each tool call carries a thought signature, which Gemini requires
  in later requests of the turn; it is stored with the step and sent back.
  Gemini accepts only a subset of JSON Schema for tools and refuses the whole
  request (HTTP 400) otherwise, so tool schemas are converted for it
  (`backend/app/providers/gemini_schema.py`): `$ref` is inlined, type lists
  become `nullable`, enums get a type, keywords such as
  `additionalProperties` are dropped and tools without parameters send none.
  The original schema is still checked by NLG before a tool runs.
- **DeepSeek**: `reasoning_content` of thinking models is sent back with the
  tool call of the same turn.

This state is stored with each step in the NLG journal (`model_state` of the
framework's ReAct history) and is never shown to the model as text. Reasoning
state is bound to the model and the exact earlier requests, so it is replayed
only while the turn's model, system prompt and tools are unchanged and no
steps were left out of the prompt. Otherwise, for example when a different
model is chosen while approving a tool call, plain tool calls and results are
sent instead.

Every provider is asked for one tool call per step (NLG runs one action per
decision).

## Permissions and approvals

Only you can change what the assistant may do. The model has no tool to change
permissions or approve its own requests.

**Risk level.** Each session has a risk level, set in the interface. It selects
the allowance used for every tool call:

| Level | Allows |
| --- | --- |
| `low` | Search, analysis, reading reports and knowledge |
| `medium` (default) | Also saving and editing reports, proposing knowledge |
| `high` | Also deleting reports |

Tools above the level are not offered to the model, and are refused if called
anyway. Lowering the level while a turn waits for approval applies when it
resumes.

**Report folder.** Report tools work only in a folder you approved for the
session. The assistant asks with `workspace.request_access` (folder and
purpose) and the turn waits for your decision. System folders are always
refused, and with `SW_REPORTS_ROOT` set, so is everything outside it. You can
revoke the folder at any time; report tools stop working immediately.

**Deleting** a report always needs your approval, even at risk level `high`.

## Tools

| Tool (model sees) | Risk | Approval | Purpose |
| --- | --- | --- | --- |
| `web.search` (`web_search`) | low | – | Web search (DuckDuckGo): titles, snippets, links |
| `data.analyze_trend` (`data_analyze_trend`) | low | – | Count, mean, median, min, max and change of JSON numbers |
| `text.keywords` (`text_keywords`) | low | – | Most frequent words of a text |
| `text.redact` (`text_redact`) | low | – | Mask email addresses and phone numbers |
| `workspace.request_access` (`workspace_request_access`) | low | every call | Ask you to approve a report folder |
| `reports.list` / `reports.read` | low | – | List reports; read `.md` and `.docx` |
| `reports.save` | medium | – | Save a new `md`, `docx` or `pdf` report; never overwrites |
| `reports.edit` | medium | – | Append, rewrite or replace one section of a `.md`/`.docx` report; keeps a backup |
| `reports.delete` | high | every call | Delete one report |
| `knowledge.list` / `read` / `search` | low | – | Read reviewed knowledge |
| `knowledge.propose` | medium | review queue | Propose a knowledge change (see below) |
| `skill.survey-creator.invoke` / `read` / `finish` | low | – | Open the survey skill, read its files, close it |
| `skill.survey-creator.script` | high | every call | Run the skill's `scripts/check_survey.py` |
| `skill.survey-creator.run_graph` | high | every graph | Run a graph saved in the skill as a child task |
| `graph.create` | high | every graph | Run a reviewed workflow graph as a child task |

Arguments are checked against each tool's JSON schema before it runs; errors
are returned to the model so it can correct the call. File names must be plain
names without folders. To add a tool, follow `skills/adding-tools/SKILL.md`.

## Skills: survey creator

`backend/skills/survey-creator/` is an [Agent Skills](https://agentskills.io/specification)
package that writes an academic survey (a literature review paper): it scopes
the review, collects and screens sources with web search, builds a taxonomy
and a comparison table, and saves a cited Markdown survey into the approved
folder. NLG registers it with `register_skill`, which turns it into ordinary
tools that go through the session's risk level like any other.

| File | Contents |
| --- | --- |
| `SKILL.md` | Instructions returned by `invoke`: ground rules (never invent sources), eight steps, the graph option |
| `templates/outline.md` | Section plan of a survey |
| `references/structure.md` | Building the taxonomy, the comparison table and the challenges |
| `references/citations.md` | Numeric citations, APA-like and GB/T 7714-2015 references |
| `graphs/survey-pipeline.json` / `.md` | The standard pipeline graph and its usage scenario, in the format NLG uses for graphs saved into a skill |
| `scripts/check_survey.py` | Checks a saved survey: sections, citation numbering, references without URLs, recency |

Ask for it in the chat, for example "写一篇关于检索增强生成的综述". What happens
depends on the risk level:

- **low / medium**: the assistant opens the skill, reads the files it needs
  and does the steps itself. Saving needs medium.
- **high**: three more tools are offered. `skill_survey-creator_run_graph`
  runs a graph saved in the skill, and `graph_create` runs an adapted one, both
  as generated graphs: you see the stages and their permissions in an
  approval card first, and the graph view follows the child task while it
  runs. The research stages use the `survey-research` allowance (search and
  skill reading only), the writing stage `survey-writer` (adds saving and
  editing reports). Then `skill_survey-creator_script` runs the check script
  on the saved file, again after your approval.

### Graphs remembered by the skill

The skill keeps the graphs it was run with, so the next survey can start from
one that worked:

1. Opening the skill lists the graphs saved in it, newest first, with a title,
   when to use each one and its stages. `survey-pipeline` ships with the skill.
2. The assistant reruns the best fit by its id with
   `skill_survey-creator_run_graph`, without copying the graph. NLG runs it
   through the skill's `SkillGraphInterface` hook, with the same review and
   permission checks as any generated graph. The approval card says which
   saved graph is being reused.
3. When a request needs a different process (for example an extra source, or
   a stage for Chinese literature), the assistant adapts the closest graph and
   asks to save it with a usage scenario. You are asked only after the adapted
   graph ran successfully; declining keeps the turn's result.

NLG also records, without the model, the environment and history of every
saved graph in `graphs/<id>.meta.json`: the environment when it was saved and
at its last successful run (node types, the tools and allowances its stages
use, and the model, which this app declares as a host fact), run counts and
the last run's outcome and error. When the skill is opened, each graph comes
with a status (`same`, `changed`, `incompatible` or `unknown`), the concrete
changes and a hint, for example that the graph last worked with another
model. The skill tells the assistant to follow that hint: reuse graphs whose
environment is the same, adapt incompatible ones, and judge changed ones.

Saved graphs are written to `graphs/<id>.json` and `.md` in the installed skill
under `SW_DATA_DIR/skills/survey-creator/`, so they survive upgrades and the
Docker volume keeps them. At start-up the bundled skill files are copied over
that folder; saved graphs are left alone. Delete a file there to forget a
graph.

NLG features this exercises: skill invocation, package-relative reads and
path checks, skill invocations in child tasks, hash-pinned scripts with human
approval, generated graphs with allowance subsets and review, child approvals
bridged to the parent, saving graphs into a skill and rerunning them through
the `SkillGraphInterface` hook,
and the skill activity in the journal and audit trail.

To add another skill, put a package under `backend/skills/<name>/`, add it to
`SKILLS` in `backend/app/tools/__init__.py` with the scripts it may run, and
add its `tool:skill.<name>.*` actions to the three risk allowances.

## Workflow graph view

The **Workflow Graph** panel shows the graph the current turn runs on and
where it is. Every node shows its status (not started, running, waiting,
completed, failed) and, while active, what it is doing: waiting for the model,
calling a tool, running a generated graph or waiting for your approval.
Finished nodes show their number of model decisions and tool calls. Generated
graphs appear under the node that created them, with their explanation, the
skill they came from and their own node states.

While a request is running, the interface polls
`GET /api/sessions/{id}/graph` every second. The backend registers an NLG run
observer on each engine, so it sees the turn's task and the child tasks of
generated graphs while they run, before they are saved. Without a running turn
the view shows the turn waiting for approval, or else the latest turn.

## Long-term knowledge

Conversations are separate, but useful conclusions can be kept. After each turn
the model may propose short knowledge entries (for example a confirmed fact from
a search). Proposals wait in the **Knowledge** panel until you accept or reject
them; nothing enters the knowledge base on its own. Proposals from turns that
used outside content (web search, report files) are marked as coming from an
external source. Accepted knowledge is offered to later turns as an index the
model can read from, and any change can be reverted through the API.

## HTTP API

| Method and path | Purpose |
| --- | --- |
| `GET /health` | Liveness check |
| `GET /api/providers` | Providers, their default models and the default provider |
| `POST /api/providers/{provider}/models` | Model IDs the key can use `{api_key}` |
| `POST /api/sessions` | Create a session |
| `GET /api/sessions/{id}` | Session: risk level, folder, messages, pending approvals |
| `GET /api/sessions/{id}/graph` | Workflow graph of the running, waiting or latest turn (`?task_id=` for another turn of the session) |
| `PUT /api/sessions/{id}/risk-level` | Set the risk level `{risk_level}` |
| `DELETE /api/sessions/{id}/workspace` | Revoke the report folder |
| `POST /api/sessions/{id}/messages` | Run a turn `{api_key, message, provider?, model?}` |
| `POST /api/sessions/{id}/approvals/{request_id}` | Decide `{api_key, approved, comment?, provider?, model?}` and continue the turn |
| `GET /api/knowledge` | Knowledge pages and pending proposals |
| `GET /api/knowledge/pages/{scope}/{slug}` | Read one page |
| `POST /api/knowledge/proposals/{id}` | Accept or reject `{approved, comment?}` |
| `POST /api/knowledge/changes/{id}/revert` | Undo an applied change |

A turn returns `status` (`completed`, `waiting` or `failed`), the `answer`,
`tool_logs`, `audit_entries`, `pending_approvals`, `stats` (models used,
model decisions, tokens and tool calls) and the updated `session`. Assistant
messages record the `provider/model` that wrote them.
While a turn waits for a decision, new messages are refused with HTTP 409.

## Data and privacy

- Your API keys are kept in the browser's local storage and sent with each
  request, only to the backend and only the key of the chosen provider. The backend uses it for that request only; it is never written to
  sessions, journals or logs. When a provider refuses a request, its reason is
  shown (for example "HTTP 400: Please pass a valid API key") with anything
  that looks like a key removed.
- `SW_DATA_DIR` holds sessions (messages, risk level, approved folder), every
  turn's journal (tool arguments and results) and the knowledge base.
- There is no login. Anyone who can reach the API can use and review every
  session; run it locally or behind your own authentication.

## Development

```bash
cd backend && python -m unittest discover -s tests -t .
```

```bash
ruff check . && ruff format --check .
```

```bash
cd frontend && npx tsc --noEmit && npm run build
```

The backend tests run offline: a scripted model replaces the providers, a stub
replaces web search, and each provider's request format is tested against
fake SDK clients that return the SDKs' own response types.

| Path | Contents |
| --- | --- |
| `backend/app/main.py` | FastAPI routes |
| `backend/app/agent.py` | Turns as NLG tasks: engine setup, running, approvals |
| `backend/app/model.py` | The adapters NLG calls, independent of the provider |
| `backend/app/providers/` | One module per model API: Chat Completions, Responses, Messages |
| `backend/app/tools/` | Tool definitions and handlers |
| `backend/app/journal.py` | Tool logs, audit trail and approvals from a turn's journal |
| `backend/app/graph_view.py`, `backend/app/live.py` | Workflow graph view and the registry of running tasks |
| `backend/skills/` | Agent Skills the assistant can use (survey-creator) |
| `backend/app/sessions.py` | Session and turn storage |
| `backend/config/` | Workflow, allowances and framework settings |
| `backend/prompts/system_prompt.md` | The assistant's instructions (Chinese) |
| `frontend/` | Next.js interface |
| `third_party/nlg/` | The NLG framework (git submodule) |
| `skills/adding-tools/` | Guide for adding tools |
| `easter-eggs/`, `frontend/public/snake/` | Snake game offered when the backend is unreachable |

## Testing the framework

This repository pins NLG at a specific commit in `third_party/nlg`. To try a
newer framework version:

```bash
git -C third_party/nlg fetch origin && git -C third_party/nlg checkout <commit>
```

Then run the backend tests and commit the new submodule commit. Framework
features exercised here: LLM nodes with ReAct, allowances and risk ceilings,
schema-checked tools, human approvals that pause and resume a saved task, the
task journal, task persistence, bounded prompts, long-term knowledge with
review, Agent Skills, generated graphs and run observers.

Gaps found while porting and fixed in the framework:

- tool arguments were not checked against `input_schema`;
- an LLM node could not run without an active-memory writer (one extra model
  call per tool call);
- the framework could not be installed with `pip install -e`;
- knowledge consolidation did not see pending proposals, so consecutive turns
  proposed the same page twice;
- a ReAct action could not carry provider state (Gemini thought signatures,
  Claude thinking blocks, GPT reasoning items) to the next decision, so Gemini
  failed on its second tool call and the others lost their reasoning between
  tool calls. Actions now have an opaque `model_state`;
- the host could not see the child task of a generated graph until the
  `graph.create` call returned, so it could neither show its progress nor
  tell which session a child task belonged to. `engine.add_run_observer` now
  reports every node execution, including child ones;
- a graph saved into a skill could not be found or rerun later: opening the
  skill did not mention it and there was no way to run it by id. Saving was
  also reviewed before the graph ran, and declining it failed the node. Now
  `skill.<name>.invoke` lists saved graphs, `skill.<name>.run_graph` runs
  them through the now-implemented `SkillGraphInterface`, saving is offered
  only after a successful run, and declining keeps the result.

Found and handled in this app, not in the framework:

- Child tasks of generated graphs carry the parent task's id, not the host's
  metadata (here, the session id). Report tools resolve a child task's session
  through the live task registry rather than trusting task data, which a
  generated graph could write to.
- ReAct history shortens every string to `prompt_max_value_chars`, which also
  cuts skill instructions and files read with `skill.<name>.read` (and a graph
  copied from a skill). `backend/config/memory_settings.json` raises it to
  16000 and `prompt_budget_chars` to 48000 so whole skill files reach the
  model. A per-tool limit in the framework would avoid making every prompt
  larger.

### Comparing models

Each turn reports which models ran it, how many decisions and tool calls it
took and how many tokens it used; the interface shows this under the message
box, and every saved turn keeps it in `task.metadata["models"]` and its
journal. To compare models, send the same task in fresh sessions with
different providers, or switch the provider mid-conversation to see how
another model continues from the same history.

## Changes from the previous version

- The model can no longer approve its own folder access or raise its own
  risk level: both were model tools before. Folder access is a real approval
  and the risk level is a session setting.
- The ten overlapping file tools are merged into five `reports.*` tools.
  Saving no longer overwrites existing files; editing names the section
  explicitly; PDF export now renders Chinese text.
- The tool registry, the AST-checked tool scaffolding script and its skill are
  removed. Tools are defined in code with schemas (see `skills/adding-tools`).
- New: tool approvals in the interface, session persistence on the server,
  audit trail from the framework journal, long-term knowledge with review.
- API: `POST /api/agent` is replaced by session endpoints. The client no longer
  sends history or permission state; the server owns both.

## Limitations

- Single user, no authentication (see [Data and privacy](#data-and-privacy)).
- One backend process: turns of a session are serialised in memory, not
  across processes.
- The assistant calls one tool per step.
- Web search depends on DuckDuckGo and can be rate-limited.
- The Gemini schema conversion follows Gemini's documented schema subset and
  is covered by offline tests; it has not yet been run against the live API.
- The graph view is polled while a request runs; with several backend
  processes it only sees turns running in the process that answers.
- Tested offline with a scripted model and fake provider clients; behaviour
  with a real model depends on the model following the instructions in
  `system_prompt.md`.
