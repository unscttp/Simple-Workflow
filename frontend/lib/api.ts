// Typed client for the backend HTTP API (see backend/app/main.py).

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000";

export type RiskLevel = "low" | "medium" | "high";

export type ProviderId = "deepseek" | "anthropic" | "openai" | "gemini";

export type Provider = {
  id: ProviderId;
  label: string;
  default_model: string;
  key_hint: string;
};

export type Providers = {
  default: ProviderId;
  providers: Provider[];
  auto_retry: { default: boolean; attempts: number };
  search: { web: string; scholar_key: boolean };
};

// The model a request runs on. The key is sent with each request and never stored by the backend.
export type ModelChoice = {
  provider: ProviderId;
  api_key: string;
  model?: string;
  auto_retry?: boolean; // retry requests that time out or fail to connect
};

export type TurnStats = {
  models: { provider: string; model: string }[];
  decisions: number;
  tokens: number;
  tokens_estimated: boolean;
  tool_calls: number;
  retries: number;
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  task_id?: string | null;
  at?: string | null;
  model?: string | null; // "provider/model" that wrote an assistant message
};

export type Approval = {
  id: string;
  kind: string;
  tool_name: string | null;
  description: string;
  risk_level: RiskLevel | "critical" | null;
  arguments: Record<string, unknown>;
  safety_context?: Record<string, unknown> | null;
  via_graph?: string | null; // set when the request comes from inside a generated graph
  explanation?: string | null;
  skill?: string | null;
  graph?: { nodes: { id: string; name: string; allowance: string | null }[]; edges: GraphEdge[] } | null;
  path?: string | null;
  skill_graph?: string | null; // a graph saved in the skill, run again
  usage_scenario?: string | null; // when to use a graph that would be saved
};

export type NodeStatus = "pending" | "ready" | "running" | "waiting" | "completed" | "failed" | "skipped";

export type NodeActivity = { kind: "model" | "tool" | "graph" | "approval"; tool?: string };

export type GraphNode = {
  id: string;
  type: string | null;
  name: string;
  allowance: string | null;
  status: NodeStatus;
  attempts: number;
  decisions: number;
  tool_calls: number;
  activity: NodeActivity | null;
  error?: string | null;
};

export type GraphEdge = { from: string; to: string; label?: string | null };

export type GraphView = {
  id: string;
  title: string;
  parent: { graph: string; node: string } | null;
  depth: number;
  explanation: string | null;
  skill: string | null;
  skill_graph: string | null;
  saved: "saved" | "declined" | null;
  status: string;
  start: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type TaskGraph = {
  task_id: string | null;
  status: string;
  running: boolean;
  graphs: GraphView[];
  current: ({ graph: string; node: string; status: NodeStatus } & NodeActivity)[];
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
  stats: TurnStats;
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
  providers: () => request<Providers>("/api/providers"),
  models: (provider: ProviderId, api_key: string) =>
    request<{ provider: ProviderId; models: string[] }>(`/api/providers/${provider}/models`, {
      method: "POST",
      body: json({ api_key }),
    }),
  sendMessage: (id: string, choice: ModelChoice, message: string) =>
    request<Turn>(`/api/sessions/${id}/messages`, {
      method: "POST",
      body: json({ ...choice, message }),
    }),
  decide: (id: string, requestId: string, choice: ModelChoice, approved: boolean, comment: string) =>
    request<Turn>(`/api/sessions/${id}/approvals/${requestId}`, {
      method: "POST",
      body: json({ ...choice, approved, comment }),
    }),
  graph: (id: string) => request<TaskGraph>(`/api/sessions/${id}/graph`),
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
