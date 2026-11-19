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
- [Permissions and approvals](#permissions-and-approvals)
- [Tools](#tools)
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
                                   ├─ model adapter ── DeepSeek / OpenAI-compatible API
                                   ├─ tools: web, data, text, reports
                                   ├─ allowances: risk-low / risk-medium / risk-high
                                   └─ knowledge store (reviewed, shared across chats)
```

- Every message you send starts one task of the `chat-turn` workflow
  (`backend/config/workflow.json`): a single LLM node that may call tools in a
  loop and then answers.
- The model is called with native tool calling. The adapter
  (`backend/app/model.py`) turns the framework's prompt into chat messages
  and tool definitions, and the model's reply into a tool call or the answer.
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

Open `http://localhost:3000`, paste your DeepSeek API key and start chatting.
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
| `SW_MODEL_BASE_URL` | `https://api.deepseek.com` | Any OpenAI-compatible chat endpoint |
| `SW_DEFAULT_MODEL` | `deepseek-chat` | Model used when a request names none |
| `SW_DEFAULT_RISK_LEVEL` | `medium` | Risk level of new sessions |
| `SW_KNOWLEDGE_CONSOLIDATION` | `true` | After each turn, ask the model what is worth remembering (one extra model call) |
| `SW_HISTORY_MESSAGES` | `20` | Earlier chat messages sent with each turn |
| `SW_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated origins allowed to call the API |

Framework settings for this app (prompt size budgets, knowledge review, limits)
are in `backend/config/memory_settings.json`; see the NLG README for each key.

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

Arguments are checked against each tool's JSON schema before it runs; errors
are returned to the model so it can correct the call. File names must be plain
names without folders. To add a tool, follow `skills/adding-tools/SKILL.md`.

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
| `POST /api/sessions` | Create a session |
| `GET /api/sessions/{id}` | Session: risk level, folder, messages, pending approvals |
| `PUT /api/sessions/{id}/risk-level` | Set the risk level `{risk_level}` |
| `DELETE /api/sessions/{id}/workspace` | Revoke the report folder |
| `POST /api/sessions/{id}/messages` | Run a turn `{api_key, message, model?}` |
| `POST /api/sessions/{id}/approvals/{request_id}` | Decide `{api_key, approved, comment?, model?}` and continue the turn |
| `GET /api/knowledge` | Knowledge pages and pending proposals |
| `GET /api/knowledge/pages/{scope}/{slug}` | Read one page |
| `POST /api/knowledge/proposals/{id}` | Accept or reject `{approved, comment?}` |
| `POST /api/knowledge/changes/{id}/revert` | Undo an applied change |

A turn returns `status` (`completed`, `waiting` or `failed`), the `answer`,
`tool_logs`, `audit_entries`, `pending_approvals` and the updated `session`.
While a turn waits for a decision, new messages are refused with HTTP 409.

## Data and privacy

- Your API key is kept in the browser's local storage and sent with each
  request. The backend uses it for that request only; it is never written to
  sessions, journals or logs, and model errors are reported without it.
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

The backend tests run offline: a scripted model replaces DeepSeek and a stub
replaces web search.

| Path | Contents |
| --- | --- |
| `backend/app/main.py` | FastAPI routes |
| `backend/app/agent.py` | Turns as NLG tasks: engine setup, running, approvals |
| `backend/app/model.py` | Model client and the adapters NLG calls |
| `backend/app/tools/` | Tool definitions and handlers |
| `backend/app/journal.py` | Tool logs, audit trail and approvals from a turn's journal |
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
task journal, task persistence, bounded prompts and long-term knowledge with
review.

Gaps found while porting and fixed in the framework:

- tool arguments were not checked against `input_schema`;
- an LLM node could not run without an active-memory writer (one extra model
  call per tool call);
- the framework could not be installed with `pip install -e`;
- knowledge consolidation did not see pending proposals, so consecutive turns
  proposed the same page twice.

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
- Tested offline with a scripted model; behaviour with a real model depends on
  the model following the instructions in `system_prompt.md`.
