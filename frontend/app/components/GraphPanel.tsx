"use client";

import type { GraphNode, GraphView, NodeActivity, NodeStatus, TaskGraph } from "../../lib/api";

const NODE_W = 168;
const NODE_H = 60;
const GAP_X = 56;
const GAP_Y = 22;
const PAD = 12;

const NODE_STYLES: Record<NodeStatus, { fill: string; stroke: string; text: string; label: string }> = {
  pending: { fill: "#f8fafc", stroke: "#cbd5e1", text: "#64748b", label: "未开始" },
  ready: { fill: "#f0f9ff", stroke: "#38bdf8", text: "#0369a1", label: "就绪" },
  running: { fill: "#fffbeb", stroke: "#f59e0b", text: "#92400e", label: "运行中" },
  waiting: { fill: "#fff7ed", stroke: "#f97316", text: "#9a3412", label: "等待" },
  completed: { fill: "#ecfdf5", stroke: "#10b981", text: "#065f46", label: "完成" },
  failed: { fill: "#fef2f2", stroke: "#ef4444", text: "#991b1b", label: "失败" },
  skipped: { fill: "#f1f5f9", stroke: "#94a3b8", text: "#64748b", label: "跳过" },
};

const GRAPH_STATUS: Record<string, string> = {
  idle: "尚未运行",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  waiting: "等待",
  awaiting_review: "等待你审核",
  starting: "启动中",
};

export function activityLabel(activity: NodeActivity): string {
  switch (activity.kind) {
    case "model":
      return "等待模型决策";
    case "graph":
      return "运行生成的子图";
    case "approval":
      return activity.tool ? `等待你确认 ${activity.tool}` : "等待你确认";
    default:
      return `调用 ${activity.tool ?? "工具"}`;
  }
}

/** Column of each node: the longest path from a start node (cycles are cut). */
function layout(graph: GraphView) {
  const level: Record<string, number> = {};
  graph.nodes.forEach((node) => (level[node.id] = graph.start.includes(node.id) ? 0 : -1));
  for (let pass = 0; pass < graph.nodes.length; pass += 1) {
    let changed = false;
    for (const edge of graph.edges) {
      const from = level[edge.from];
      if (from >= 0 && from + 1 > level[edge.to] && from + 1 < graph.nodes.length) {
        level[edge.to] = from + 1;
        changed = true;
      }
    }
    if (!changed) break;
  }
  const columns: GraphNode[][] = [];
  for (const node of graph.nodes) {
    const column = Math.max(level[node.id], 0);
    (columns[column] ??= []).push(node);
  }
  const position: Record<string, { x: number; y: number }> = {};
  columns.forEach((nodes, column) =>
    nodes.forEach((node, row) => {
      position[node.id] = { x: PAD + column * (NODE_W + GAP_X), y: PAD + row * (NODE_H + GAP_Y) };
    }),
  );
  const rows = Math.max(1, ...columns.map((nodes) => nodes?.length ?? 0));
  return {
    position,
    width: PAD * 2 + columns.length * NODE_W + Math.max(0, columns.length - 1) * GAP_X,
    height: PAD * 2 + rows * NODE_H + (rows - 1) * GAP_Y,
  };
}

function shorten(text: string, max: number) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function Graph({ graph }: { graph: GraphView }) {
  const { position, width, height } = layout(graph);
  const markerId = `arrow-${graph.id}`;
  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height + 8}`}
        style={{ width: "100%", maxWidth: width, minWidth: Math.min(width, 520), height: "auto" }}
        role="img"
        aria-label={`${graph.title}：${graph.nodes.map((n) => `${n.name} ${NODE_STYLES[n.status].label}`).join("，")}`}
      >
        <defs>
          <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8" />
          </marker>
        </defs>
        {graph.edges.map((edge, index) => {
          const from = position[edge.from];
          const to = position[edge.to];
          if (!from || !to) return null;
          const x1 = from.x + NODE_W;
          const y1 = from.y + NODE_H / 2;
          const x2 = to.x;
          const y2 = to.y + NODE_H / 2;
          const back = x2 <= x1;
          const d = back
            ? `M${x1},${y1} C${x1 + 40},${y1 - 50} ${x2 - 40},${y2 - 50} ${x2},${y2}`
            : `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
          const done = graph.nodes.find((n) => n.id === edge.from)?.status === "completed";
          return (
            <path
              key={`${edge.from}-${edge.to}-${index}`}
              d={d}
              fill="none"
              stroke={done ? "#10b981" : "#cbd5e1"}
              strokeWidth={done ? 2 : 1.5}
              markerEnd={`url(#${markerId})`}
            />
          );
        })}
        {graph.nodes.map((node) => {
          const { x, y } = position[node.id];
          const style = NODE_STYLES[node.status];
          const active = node.status === "running" || node.status === "waiting";
          const detail = node.activity
            ? activityLabel(node.activity)
            : node.status === "completed" && node.decisions
              ? `${node.decisions} 次决策 · ${node.tool_calls} 次工具`
              : style.label;
          return (
            <g key={node.id}>
              <title>
                {`${node.name} (${node.id})\n状态：${style.label}${node.allowance ? `\n权限：${node.allowance}` : ""}${
                  node.error ? `\n错误：${node.error}` : ""
                }`}
              </title>
              {active && (
                <rect
                  x={x - 4}
                  y={y - 4}
                  width={NODE_W + 8}
                  height={NODE_H + 8}
                  rx={16}
                  fill="none"
                  stroke={style.stroke}
                  strokeOpacity={0.45}
                  strokeWidth={3}
                  className="animate-pulse"
                />
              )}
              <rect
                x={x}
                y={y}
                width={NODE_W}
                height={NODE_H}
                rx={12}
                fill={style.fill}
                stroke={style.stroke}
                strokeWidth={active ? 2 : 1.5}
                strokeDasharray={node.status === "skipped" ? "4 3" : undefined}
              />
              <text x={x + 12} y={y + 23} fontSize={13} fontWeight={600} fill="#0f172a">
                {shorten(node.name, 16)}
              </text>
              <text x={x + 12} y={y + 44} fontSize={11} fill={style.text}>
                {shorten(detail, 22)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function GraphPanel({ graph }: { graph: TaskGraph | null }) {
  if (!graph) {
    return null;
  }
  const names = Object.fromEntries(
    graph.graphs.flatMap((g) => g.nodes.map((n) => [`${g.id}/${n.id}`, n.name] as const)),
  );
  return (
    <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50 p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-700">Workflow Graph</h2>
        <span className="text-xs text-slate-400">
          {graph.running ? "实时更新中" : GRAPH_STATUS[graph.status] ?? graph.status}
        </span>
      </div>
      {graph.current.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-slate-700">
          {graph.current.map((item) => (
            <li key={`${item.graph}/${item.node}`}>
              <span className="font-semibold">{names[`${item.graph}/${item.node}`] ?? item.node}</span>
              ：{activityLabel(item)}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 space-y-4">
        {graph.graphs.map((g) => (
          <div
            key={g.id}
            className={g.parent ? "rounded-xl border border-dashed border-slate-300 bg-white/70 p-3" : ""}
            style={g.parent ? { marginLeft: Math.min(g.depth, 3) * 12 } : undefined}
          >
            {g.parent && (
              <div className="mb-2 text-xs text-slate-600">
                <p>
                  <span className="font-semibold text-slate-800">生成的子图</span> · 由{" "}
                  <span className="font-mono">{names[`${g.parent.graph}/${g.parent.node}`] ?? g.parent.node}</span>{" "}
                  创建
                  {g.skill && (
                    <>
                      {" "}
                      · 技能 <span className="font-mono">{g.skill}</span>
                    </>
                  )}{" "}
                  · {GRAPH_STATUS[g.status] ?? g.status}
                  {g.skill_graph && (
                    <>
                      {" "}
                      · 复用保存的图 <span className="font-mono">{g.skill_graph}</span>
                    </>
                  )}
                  {g.saved && <> · {g.saved === "saved" ? "已保存到技能" : "未保存"}</>}
                </p>
                {g.explanation && <p className="mt-1 text-slate-500">{g.explanation}</p>}
              </div>
            )}
            <Graph graph={g} />
          </div>
        ))}
      </div>
    </div>
  );
}
