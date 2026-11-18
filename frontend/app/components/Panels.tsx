"use client";

import { useState } from "react";

import type {
  Approval,
  AuditEntry,
  Knowledge,
  KnowledgeProposal,
  RiskLevel,
  Session,
  ToolLog,
} from "../../lib/api";

const RISK_LABELS: Record<RiskLevel, string> = {
  low: "只读：搜索、分析、读取报告",
  medium: "可保存和编辑报告",
  high: "全部工具，包括删除报告",
};

const TOOL_LABELS: Record<string, string> = {
  "workspace.request_access": "申请报告目录权限",
  "reports.delete": "删除报告",
};

function Card({ title, children, extra }: { title: string; children: React.ReactNode; extra?: string }) {
  return (
    <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-700">{title}</h2>
        {extra && <span className="text-xs text-slate-400">{extra}</span>}
      </div>
      <div className="mt-3 space-y-3">{children}</div>
    </div>
  );
}

export function RiskSelector({
  session,
  disabled,
  onChange,
}: {
  session: Session | null;
  disabled: boolean;
  onChange: (level: RiskLevel) => void;
}) {
  return (
    <div className="mt-4 rounded-2xl border border-indigo-200 bg-indigo-50 p-4 text-xs text-indigo-900">
      <p className="font-semibold text-indigo-950">风险级别</p>
      <p className="mt-1 text-indigo-800">只能由你设置，Agent 无法修改。每次工具调用都会按当前级别检查。</p>
      <div className="mt-3 flex gap-2">
        {(["low", "medium", "high"] as RiskLevel[]).map((level) => (
          <button
            key={level}
            type="button"
            onClick={() => onChange(level)}
            disabled={disabled || !session}
            className={`rounded-full px-3 py-1.5 text-xs font-semibold transition disabled:opacity-60 ${
              session?.risk_level === level
                ? "bg-indigo-600 text-white"
                : "border border-indigo-300 bg-white text-indigo-900 hover:bg-indigo-100"
            }`}
          >
            {level}
          </button>
        ))}
      </div>
      {session && <p className="mt-2 text-indigo-700">{RISK_LABELS[session.risk_level]}</p>}
    </div>
  );
}

export function WorkspacePanel({
  session,
  disabled,
  onRevoke,
}: {
  session: Session | null;
  disabled: boolean;
  onRevoke: () => void;
}) {
  return (
    <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs text-slate-700">
      <p className="font-semibold text-slate-900">已授权的报告目录</p>
      <p className="mt-1 break-all font-mono">{session?.workspace ?? "未授权"}</p>
      <p className="mt-1 text-slate-500">Agent 需要保存报告时会申请目录，由你在下方确认。</p>
      {session?.workspace && (
        <button
          type="button"
          onClick={onRevoke}
          disabled={disabled}
          className="mt-3 rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
        >
          撤销授权
        </button>
      )}
    </div>
  );
}

export function ApprovalCard({
  approval,
  disabled,
  onDecide,
}: {
  approval: Approval;
  disabled: boolean;
  onDecide: (approved: boolean, comment: string) => void;
}) {
  const [comment, setComment] = useState("");
  const label = (approval.tool_name && TOOL_LABELS[approval.tool_name]) || approval.tool_name || approval.kind;
  return (
    <div className="mt-4 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-4 text-sm text-amber-900">
      <p className="font-semibold">需要你的确认：{label}</p>
      {approval.description && <p className="mt-1 text-xs text-amber-800">{approval.description}</p>}
      <dl className="mt-3 space-y-1 text-xs">
        {Object.entries(approval.arguments).map(([key, value]) => (
          <div key={key} className="flex gap-2">
            <dt className="font-semibold">{key}</dt>
            <dd className="break-all font-mono">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
          </div>
        ))}
        {approval.risk_level && (
          <div className="flex gap-2">
            <dt className="font-semibold">风险</dt>
            <dd className="font-mono">{approval.risk_level}</dd>
          </div>
        )}
      </dl>
      <input
        value={comment}
        onChange={(event) => setComment(event.target.value)}
        placeholder="备注（可选）"
        className="mt-3 w-full rounded-xl border border-amber-300 bg-white px-3 py-2 text-xs text-slate-800 outline-none focus:border-amber-500"
      />
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => onDecide(true, comment)}
          disabled={disabled}
          className="rounded-full bg-emerald-500 px-4 py-2 text-xs font-semibold text-white disabled:opacity-60"
        >
          允许
        </button>
        <button
          type="button"
          onClick={() => onDecide(false, comment)}
          disabled={disabled}
          className="rounded-full bg-red-500 px-4 py-2 text-xs font-semibold text-white disabled:opacity-60"
        >
          拒绝
        </button>
      </div>
    </div>
  );
}

const STATUS_STYLES: Record<ToolLog["status"], [string, string]> = {
  completed: ["成功", "bg-emerald-100 text-emerald-700"],
  failed: ["失败", "bg-red-100 text-red-700"],
  waiting: ["等待确认", "bg-amber-100 text-amber-700"],
  approved: ["已批准", "bg-sky-100 text-sky-700"],
  rejected: ["已拒绝", "bg-red-100 text-red-700"],
  requested: ["已请求", "bg-slate-200 text-slate-700"],
};

export function ToolLogs({ logs }: { logs: ToolLog[] }) {
  return (
    <Card title="Tool Logs" extra={`${logs.length} 条`}>
      {logs.length === 0 ? (
        <p className="text-sm text-slate-500">本轮还没有工具调用。</p>
      ) : (
        logs.map((log) => {
          const [label, style] = STATUS_STYLES[log.status];
          return (
            <div key={log.id} className="rounded-2xl bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <p className="font-mono text-sm text-slate-900">{log.tool_name}</p>
                <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${style}`}>{label}</span>
              </div>
              <pre className="mt-3 whitespace-pre-wrap rounded-xl bg-slate-950 p-3 text-xs text-slate-100">
                {JSON.stringify(log.arguments, null, 2)}
              </pre>
              <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap text-sm text-slate-700">{log.output}</pre>
            </div>
          );
        })
      )}
    </Card>
  );
}

export function AuditTrail({ entries }: { entries: AuditEntry[] }) {
  return (
    <Card title="Audit Trail" extra={`${entries.length} 条`}>
      {entries.length === 0 ? (
        <p className="text-sm text-slate-500">暂无审计记录。</p>
      ) : (
        entries.map((entry, index) => (
          <div key={`${entry.timestamp}-${index}`} className="rounded-2xl bg-white p-4 shadow-sm">
            <p className="text-xs text-slate-500">{entry.timestamp}</p>
            <p className="mt-1 text-sm font-semibold text-slate-900">{entry.summary}</p>
            <p className="mt-1 font-mono text-xs text-slate-500">{entry.type}</p>
          </div>
        ))
      )}
    </Card>
  );
}

function ProposalCard({
  proposal,
  onReview,
}: {
  proposal: KnowledgeProposal;
  onReview: (id: string, approved: boolean) => void;
}) {
  const external = proposal.source.kind === "external";
  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-slate-900">{proposal.rationale}</p>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
            external ? "bg-orange-100 text-orange-700" : "bg-slate-200 text-slate-700"
          }`}
        >
          {external ? "含外部来源" : "内部来源"}
        </span>
      </div>
      {external && proposal.source.external_actions && (
        <p className="mt-1 text-xs text-orange-700">来源工具：{proposal.source.external_actions.join(", ")}</p>
      )}
      <ul className="mt-2 space-y-1 text-xs text-slate-700">
        {proposal.operations.map((operation, index) => (
          <li key={index}>
            <span className="font-mono">{operation.op}</span> <span className="font-mono">{operation.page}</span>
            {typeof operation.text === "string" && <span>：{operation.text}</span>}
            {typeof operation.title === "string" && <span>：{operation.title}</span>}
          </li>
        ))}
      </ul>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => onReview(proposal.id, true)}
          className="rounded-full bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white"
        >
          采纳
        </button>
        <button
          type="button"
          onClick={() => onReview(proposal.id, false)}
          className="rounded-full bg-red-500 px-3 py-1.5 text-xs font-semibold text-white"
        >
          拒绝
        </button>
      </div>
    </div>
  );
}

export function KnowledgePanel({
  knowledge,
  onReview,
  onOpenPage,
  openPage,
}: {
  knowledge: Knowledge;
  onReview: (id: string, approved: boolean) => void;
  onOpenPage: (page: string) => void;
  openPage: { page: string; content: string } | null;
}) {
  return (
    <Card title="Knowledge" extra={`${knowledge.pages.length} 页 · ${knowledge.proposals.length} 待审`}>
      <p className="text-xs text-slate-500">
        Agent 在对话结束后提出值得长期保留的结论，经你审核后才会在以后的对话中使用。
      </p>
      {knowledge.proposals.map((proposal) => (
        <ProposalCard key={proposal.id} proposal={proposal} onReview={onReview} />
      ))}
      {knowledge.pages.map((page) => (
        <button
          key={page.page}
          type="button"
          onClick={() => onOpenPage(page.page)}
          className="block w-full rounded-2xl bg-white p-4 text-left shadow-sm hover:bg-slate-100"
        >
          <p className="text-sm font-semibold text-slate-900">
            {page.title} <span className="font-mono text-xs text-slate-400">{page.page}</span>
          </p>
          <p className="mt-1 text-xs text-slate-600">
            {page.summary} · {page.entries} 条
          </p>
          {openPage?.page === page.page && (
            <pre className="mt-3 whitespace-pre-wrap text-xs text-slate-700">{openPage.content}</pre>
          )}
        </button>
      ))}
    </Card>
  );
}
