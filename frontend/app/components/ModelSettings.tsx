"use client";

import { useEffect, useState } from "react";

import { api, type ModelChoice, type Provider, type ProviderId, type TurnStats } from "../../lib/api";

// Everything here stays in this browser's localStorage: one key and one model per provider.
const PROVIDER_KEY = "model_provider";
const keyName = (provider: ProviderId) => `api_key:${provider}`;
const modelName = (provider: ProviderId) => `model:${provider}`;
const LEGACY_DEEPSEEK_KEY = "deepseek_api_key";

function stored(name: string): string {
  try {
    return window.localStorage.getItem(name) ?? "";
  } catch {
    return "";
  }
}

function store(name: string, value: string) {
  try {
    if (value) {
      window.localStorage.setItem(name, value);
    } else {
      window.localStorage.removeItem(name);
    }
  } catch {
    // Private windows can refuse storage; the values then last for this page only.
  }
}

/** The provider, key and model the next request uses; `null` until a key is entered. */
export function useModelChoice(providers: Provider[], defaultProvider: ProviderId | null) {
  const [provider, setProvider] = useState<ProviderId | null>(null);
  const [keys, setKeys] = useState<Partial<Record<ProviderId, string>>>({});
  const [models, setModels] = useState<Partial<Record<ProviderId, string>>>({});

  useEffect(() => {
    if (!providers.length) {
      return;
    }
    const legacy = stored(LEGACY_DEEPSEEK_KEY);
    if (legacy && !stored(keyName("deepseek"))) {
      store(keyName("deepseek"), legacy);
    }
    store(LEGACY_DEEPSEEK_KEY, "");
    setKeys(Object.fromEntries(providers.map((p) => [p.id, stored(keyName(p.id))])));
    setModels(Object.fromEntries(providers.map((p) => [p.id, stored(modelName(p.id))])));
    const saved = stored(PROVIDER_KEY) as ProviderId;
    setProvider(providers.some((p) => p.id === saved) ? saved : (defaultProvider ?? providers[0].id));
  }, [providers, defaultProvider]);

  const update = {
    provider: (id: ProviderId) => {
      setProvider(id);
      store(PROVIDER_KEY, id);
    },
    key: (id: ProviderId, value: string) => {
      setKeys((current) => ({ ...current, [id]: value }));
      store(keyName(id), value.trim());
    },
    model: (id: ProviderId, value: string) => {
      setModels((current) => ({ ...current, [id]: value }));
      store(modelName(id), value.trim());
    },
  };

  const key = provider ? (keys[provider] ?? "").trim() : "";
  const model = provider ? (models[provider] ?? "").trim() : "";
  const choice: ModelChoice | null =
    provider && key ? { provider, api_key: key, ...(model ? { model } : {}) } : null;
  return { provider, keys, models, update, choice };
}

export function ModelSettings({
  providers,
  state,
  disabled,
}: {
  providers: Provider[];
  state: ReturnType<typeof useModelChoice>;
  disabled: boolean;
}) {
  const { provider, keys, models, update } = state;
  const [available, setAvailable] = useState<Partial<Record<ProviderId, string[]>>>({});
  const [listing, setListing] = useState(false);
  const [listError, setListError] = useState("");
  const current = providers.find((p) => p.id === provider);

  if (!current) {
    return <p className="mt-6 text-sm text-slate-500">正在读取可用的模型服务...</p>;
  }

  async function loadModels(id: ProviderId) {
    setListing(true);
    setListError("");
    try {
      const listed = await api.models(id, (keys[id] ?? "").trim());
      setAvailable((known) => ({ ...known, [id]: listed.models }));
    } catch (error) {
      setListError(error instanceof Error ? error.message : "读取模型列表失败。");
    } finally {
      setListing(false);
    }
  }

  const options = available[current.id] ?? [];
  return (
    <div className="mt-6 space-y-3">
      <div>
        <label className="block text-sm font-medium text-slate-700" htmlFor="provider">
          模型服务
        </label>
        <select
          id="provider"
          value={current.id}
          onChange={(event) => {
            update.provider(event.target.value as ProviderId);
            setListError("");
          }}
          disabled={disabled}
          className="mt-2 w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm outline-none focus:border-amber-400 focus:bg-white"
        >
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-700" htmlFor="api-key">
          {current.label} API Key
        </label>
        <input
          id="api-key"
          type="password"
          autoComplete="off"
          value={keys[current.id] ?? ""}
          onChange={(event) => update.key(current.id, event.target.value)}
          placeholder={current.key_hint}
          className="mt-2 w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2.5 font-mono text-sm outline-none focus:border-amber-400 focus:bg-white"
        />
      </div>

      <div>
        <div className="flex items-center justify-between">
          <label className="block text-sm font-medium text-slate-700" htmlFor="model">
            模型
          </label>
          <button
            type="button"
            onClick={() => loadModels(current.id)}
            disabled={disabled || listing || !(keys[current.id] ?? "").trim()}
            className="text-xs font-semibold text-amber-700 hover:text-amber-600 disabled:text-slate-400"
          >
            {listing ? "读取中..." : "读取可用模型"}
          </button>
        </div>
        <input
          id="model"
          list="model-options"
          value={models[current.id] ?? ""}
          onChange={(event) => update.model(current.id, event.target.value)}
          placeholder={`默认：${current.default_model}`}
          className="mt-2 w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2.5 font-mono text-sm outline-none focus:border-amber-400 focus:bg-white"
        />
        <datalist id="model-options">
          {options.map((id) => (
            <option key={id} value={id} />
          ))}
        </datalist>
        {options.length > 0 && (
          <p className="mt-1 text-xs text-slate-500">已读取 {options.length} 个模型，可在输入框中选择。</p>
        )}
        {listError && <p className="mt-1 text-xs text-red-600">{listError}</p>}
      </div>
    </div>
  );
}

export function TurnStatsLine({ stats }: { stats: TurnStats | null }) {
  if (!stats) {
    return null;
  }
  const models = stats.models.map((m) => `${m.provider}/${m.model}`).join(" → ");
  return (
    <p className="mt-2 text-xs text-slate-500">
      上一轮：<span className="font-mono">{models || "—"}</span> · {stats.decisions} 次决策 ·{" "}
      {stats.tool_calls} 次工具调用 · {stats.tokens_estimated ? "约 " : ""}
      {stats.tokens.toLocaleString()} tokens
    </p>
  );
}
