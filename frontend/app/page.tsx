"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  API_BASE_URL,
  ApiError,
  api,
  isNetworkError,
  type AuditEntry,
  type Knowledge,
  type Providers,
  type RiskLevel,
  type Session,
  type TaskGraph,
  type ToolLog,
  type Turn,
  type TurnStats,
} from "../lib/api";
import { GraphPanel } from "./components/GraphPanel";
import { ModelSettings, TurnStatsLine, useModelChoice } from "./components/ModelSettings";
import {
  ApprovalCard,
  AuditTrail,
  KnowledgePanel,
  RiskSelector,
  ToolLogs,
  WorkspacePanel,
} from "./components/Panels";

const SESSION_KEY = "agent_session_id";
const NETWORK_FALLBACK_MESSAGE = "网络出现问题，需要玩一会贪吃蛇来等待一下吗？";
const WELCOME =
  "这里是一个轻量级 AI Agent。先在左侧选择模型服务（DeepSeek、Claude、GPT 或 Gemini）并填入 API Key，再让我搜索资料、分析 JSON 数据或整理报告。保存报告前我会先申请目录权限，由你确认。";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误，请稍后再试。";
}

export default function HomePage() {
  const [session, setSession] = useState<Session | null>(null);
  const [providers, setProviders] = useState<Providers | null>(null);
  const modelChoice = useModelChoice(providers?.providers ?? [], providers?.default ?? null);
  const [stats, setStats] = useState<TurnStats | null>(null);
  const [graph, setGraph] = useState<TaskGraph | null>(null);
  const [input, setInput] = useState("");
  const [toolLogs, setToolLogs] = useState<ToolLog[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [knowledge, setKnowledge] = useState<Knowledge>({ pages: [], proposals: [] });
  const [openPage, setOpenPage] = useState<{ page: string; content: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshGraph = useCallback(async (sessionId: string) => {
    try {
      setGraph(await api.graph(sessionId));
    } catch {
      // The graph is a view of the turn; chat keeps working without it.
    }
  }, []);

  const sessionId = session?.id;
  useEffect(() => {
    if (!sessionId) {
      return;
    }
    void refreshGraph(sessionId);
    if (!busy) {
      return;
    }
    // While a turn runs, follow it: the backend reports the node and step it is on.
    const timer = window.setInterval(() => void refreshGraph(sessionId), 1000);
    return () => window.clearInterval(timer);
  }, [sessionId, busy, refreshGraph]);

  const refreshKnowledge = useCallback(async () => {
    try {
      setKnowledge(await api.knowledge());
    } catch {
      // The knowledge panel is optional; chat keeps working without it.
    }
  }, []);

  useEffect(() => {
    api
      .providers()
      .then(setProviders)
      .catch((loadError) => setError(`无法读取模型服务列表：${errorMessage(loadError)}`));
    const saved = window.localStorage.getItem(SESSION_KEY);
    const load = saved ? api.getSession(saved) : Promise.reject(new ApiError("no session", 404));
    load
      .catch((loadError) => {
        if (loadError instanceof ApiError && loadError.status === 404) {
          return api.createSession();
        }
        throw loadError;
      })
      .then((loaded) => {
        window.localStorage.setItem(SESSION_KEY, loaded.id);
        setSession(loaded);
      })
      .catch((loadError) => setError(`无法连接后端：${errorMessage(loadError)}`));
    void refreshKnowledge();
  }, [refreshKnowledge]);

  function applyTurn(turn: Turn) {
    setSession(turn.session);
    setToolLogs(turn.tool_logs);
    setAuditEntries(turn.audit_entries);
    setStats(turn.stats);
    setError(turn.status === "failed" && turn.error ? `本轮执行失败：${turn.error}` : "");
    void refreshKnowledge();
  }

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (actionError) {
      setError(errorMessage(actionError));
      if (isNetworkError(actionError) && window.confirm(NETWORK_FALLBACK_MESSAGE)) {
        window.location.href = "/snake/";
      }
    } finally {
      setBusy(false);
    }
  }

  function requireChoice() {
    if (!modelChoice.choice) {
      setError("请先选择模型服务并填写它的 API Key。");
    }
    return modelChoice.choice;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = input.trim();
    const choice = session && message ? requireChoice() : null;
    if (!session || !choice) {
      return;
    }
    setSession({ ...session, messages: [...session.messages, { role: "user", content: message }] });
    setInput("");
    await run(async () => applyTurn(await api.sendMessage(session.id, choice, message)));
  }

  async function handleDecision(requestId: string, approved: boolean, comment: string) {
    const choice = session ? requireChoice() : null;
    if (!session || !choice) {
      return;
    }
    await run(async () =>
      applyTurn(await api.decide(session.id, requestId, choice, approved, comment)),
    );
  }

  async function handleRiskLevel(level: RiskLevel) {
    if (session) {
      await run(async () => setSession(await api.setRiskLevel(session.id, level)));
    }
  }

  async function handleRevoke() {
    if (session) {
      await run(async () => setSession(await api.revokeWorkspace(session.id)));
    }
  }

  async function handleReview(proposalId: string, approved: boolean) {
    await run(async () => {
      await api.reviewProposal(proposalId, approved);
      await refreshKnowledge();
    });
  }

  async function handleOpenPage(page: string) {
    if (openPage?.page === page) {
      setOpenPage(null);
      return;
    }
    await run(async () => {
      const loaded = await api.knowledgePage(page);
      setOpenPage({ page, content: loaded.content });
    });
  }

  const pending = session?.pending_approvals ?? [];
  const messages = session?.messages ?? [];

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_#fef3c7,_#fff7ed_35%,_#ffffff_70%)] px-4 py-10 text-slate-900">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <section className="rounded-3xl border border-amber-200 bg-white/85 p-6 shadow-[0_24px_80px_-32px_rgba(180,83,9,0.35)] backdrop-blur">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-700">Agent Settings</p>
          <h1 className="mt-3 font-serif text-3xl text-slate-950">轻量级 AI Agent</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            每个服务的 API Key 只保存在浏览器本地，每次请求时发给后端调用模型，后端不会保存。可以随时切换服务，比较不同模型的表现。
          </p>

          <ModelSettings providers={providers?.providers ?? []} state={modelChoice} disabled={busy} />

          <RiskSelector session={session} disabled={busy || pending.length > 0} onChange={handleRiskLevel} />
          <WorkspacePanel session={session} disabled={busy} onRevoke={handleRevoke} />

          <div className="mt-6 rounded-2xl border border-dashed border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
            <p className="font-medium">可直接尝试：</p>
            <p className="mt-2">1. 搜索一下最近 AI Agent 的热门趋势</p>
            <p className="mt-1">
              2. 分析这段 JSON 数据：<code>{`[{"value":12}, {"value":30}]`}</code>
            </p>
            <p className="mt-1">3. 整理结论并保存成一份 Markdown 报告</p>
          </div>
        </section>

        <section className="rounded-3xl border border-slate-200 bg-white/90 p-5 shadow-[0_24px_80px_-32px_rgba(15,23,42,0.3)] backdrop-blur">
          <div className="flex min-h-[60vh] flex-col">
            <div className="flex-1 space-y-4 overflow-y-auto rounded-2xl bg-slate-50/80 p-4">
              {[{ role: "assistant" as const, content: WELCOME }, ...messages].map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm ${
                    message.role === "user" ? "ml-auto bg-slate-900 text-white" : "bg-white text-slate-800"
                  }`}
                >
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.24em] opacity-60">
                    {message.role === "user" ? "User" : "Agent"}
                    {message.model && (
                      <span className="ml-2 font-mono normal-case tracking-normal">{message.model}</span>
                    )}
                  </p>
                  <pre className="whitespace-pre-wrap font-sans">{message.content}</pre>
                </div>
              ))}
              {busy && (
                <div className="max-w-[85%] rounded-2xl bg-white px-4 py-3 text-sm text-slate-500 shadow-sm">
                  Agent 正在思考并可能调用工具，请稍候...
                </div>
              )}
            </div>

            {pending.map((approval) => (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                disabled={busy}
                onDecide={(approved, comment) => handleDecision(approval.id, approved, comment)}
              />
            ))}

            <form onSubmit={handleSubmit} className="mt-4 space-y-3">
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={pending.length ? "请先在上方确认或拒绝待处理的操作。" : "输入任务，例如：先搜索 Agent 热词，再整理为报告。"}
                disabled={pending.length > 0}
                className="min-h-28 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition focus:border-amber-400 disabled:bg-slate-100"
              />
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-slate-500">
                  后端地址：<span className="font-mono">{API_BASE_URL}</span>
                </p>
                <button
                  type="submit"
                  disabled={busy || !session || pending.length > 0}
                  className="rounded-full bg-amber-500 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-amber-400 disabled:cursor-not-allowed disabled:bg-amber-200"
                >
                  {busy ? "处理中..." : "发送"}
                </button>
              </div>
              <TurnStatsLine stats={stats} />
            </form>

            {error && (
              <div className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {error}
              </div>
            )}

            <GraphPanel graph={graph} />
            <ToolLogs logs={toolLogs} />
            <AuditTrail entries={auditEntries} />
            <KnowledgePanel
              knowledge={knowledge}
              onReview={handleReview}
              onOpenPage={handleOpenPage}
              openPage={openPage}
            />
          </div>
        </section>
      </div>
    </main>
  );
}
