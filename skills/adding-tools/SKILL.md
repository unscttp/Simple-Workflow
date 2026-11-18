---
name: adding-tools
description: Add or change a tool of the Simple Workflow assistant (backend/app/tools) so that it is schema-checked, permission-gated by the session risk level and covered by tests.
---

# Adding a tool to the assistant

Tools are NLG `ToolDefinition`s registered on one `LocalToolModule` in
`backend/app/tools/__init__.py`. The framework checks every call against the
tool's `input_schema`, the session's risk allowance and any human approval
before the handler runs, and records the call in the turn's journal.

## Steps

1. **Define the tool** in the module of its area (`research.py`, `analysis.py`,
   `reports.py`) or a new module in `backend/app/tools/`:
   - `name`: lower-case dotted, such as `reports.export`. The model sees it as
     `reports_export` (dots become underscores).
   - `risk_level`: `low` reads or computes, `medium` creates or changes files,
     `high` deletes or is irreversible.
   - `human_check=True` when every call needs the user's approval.
   - `input_schema`: an object schema with `required` and
     `"additionalProperties": false`; bound strings with `maxLength`.
2. **Write the handler** `handler(context, tool, args) -> dict`. Return JSON
   data. Raise an exception for errors; its message is shown to the model, so
   make it say how to fix the call. Record notable side effects with
   `context.record_activity("report.exported", {...})` and add the event type to
   `AUDIT_EVENT_TYPES` in `backend/app/journal.py` to show it in the audit trail.
3. **Register it** in `build_tools` (`backend/app/tools/__init__.py`).
4. **Allow it**: add `"tool:<name>"` to all three files in
   `backend/config/allowances/`. The risk level, not the list, decides which
   sessions can use it. `ConfigurationTests` fails until all three list it.
5. **Tell the model** when to use it in `backend/prompts/system_prompt.md`,
   using the underscore name.
6. **Test it**: unit tests in `backend/tests/test_tools.py`, and a flow through
   the API with the scripted model in `backend/tests/test_api.py`.
7. If the tool reads content from outside the application (web, files, other
   services), leave it out of `TRUSTED_ACTIONS`, so knowledge proposals from
   turns that used it always need review.

## Checks

```bash
cd backend && python -m unittest discover -s tests -t .
```

```bash
ruff check . && ruff format --check .
```
