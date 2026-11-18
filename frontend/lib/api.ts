// Typed client for the backend HTTP API (see backend/app/main.py).

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000";

export type RiskLevel = "low" | "medium" | "high";

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  task_id?: string | null;
  at?: string | null;
};

export type Approval = {
  id: string;
  kind: string;
  tool_name: string | null;
  description: string;
  risk_level: RiskLevel | "critical" | null;
  arguments: Record<string, unknown>;
  safety_context?: Record<string, unknown> | null;
};

export type Session = {
  id: string;
  risk_level: RiskLevel;
  workspace: string | null;
  messages: ChatMessage[];
  pending_approvals: Approval[];
};

export type ToolLog = {
  id: string;
  timestamp: string;
  tool_name: string;
  arguments: unknown;
  status: "requested" | "completed" | "failed" | "waiting" | "approved" | "rejected";
  output: string;
};

export type AuditEntry = {
  timestamp: string;
  type: string;
  summary: string;
};

export type Turn = {
  status: "completed" | "waiting" | "failed";
  task_id: string;
  answer: string | null;
  error: string | null;
  tool_logs: ToolLog[];
  audit_entries: AuditEntry[];
  pending_approvals: Approval[];
  session: Session;
};

export type KnowledgePage = {
  page: string;
  title: string;
  summary: string;
  entries: number;
  updated_at: string;
};

export type KnowledgeOperation = {
  op: string;
  page: string;
  [field: string]: unknown;
};

export type KnowledgeProposal = {
  id: string;
  created_at: string;
  rationale: string;
  operations: KnowledgeOperation[];
  source: { kind: "internal" | "external" | "host"; external_actions?: string[]; task_id?: string };
};

export type Knowledge = {
  pages: KnowledgePage[];
  proposals: KnowledgeProposal[];
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `请求失败（HTTP ${response.status}）`;
    throw new ApiError(detail, response.status);
  }
  return body as T;
}

const json = (value: unknown) => JSON.stringify(value);

export const api = {
  createSession: () => request<Session>("/api/sessions", { method: "POST" }),
  getSession: (id: string) => request<Session>(`/api/sessions/${id}`),
  setRiskLevel: (id: string, risk_level: RiskLevel) =>
    request<Session>(`/api/sessions/${id}/risk-level`, { method: "PUT", body: json({ risk_level }) }),
  revokeWorkspace: (id: string) =>
    request<Session>(`/api/sessions/${id}/workspace`, { method: "DELETE" }),
  sendMessage: (id: string, api_key: string, message: string) =>
    request<Turn>(`/api/sessions/${id}/messages`, {
      method: "POST",
      body: json({ api_key, message }),
    }),
  decide: (id: string, requestId: string, api_key: string, approved: boolean, comment: string) =>
    request<Turn>(`/api/sessions/${id}/approvals/${requestId}`, {
      method: "POST",
      body: json({ api_key, approved, comment }),
    }),
  knowledge: () => request<Knowledge>("/api/knowledge"),
  knowledgePage: (page: string) =>
    request<{ page: string; version: number; content: string }>(`/api/knowledge/pages/${page}`),
  reviewProposal: (id: string, approved: boolean) =>
    request<unknown>(`/api/knowledge/proposals/${id}`, {
      method: "POST",
      body: json({ approved, comment: "" }),
    }),
};

export function isNetworkError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return false;
  }
  if (error instanceof TypeError) {
    return true;
  }
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  return ["failed to fetch", "networkerror", "load failed", "network request failed"].some((text) =>
    message.includes(text),
  );
}
