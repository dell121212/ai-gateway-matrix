export type UsageSummary = {
  period: string; requests: number; successes: number; failures: number;
  success_rate: number; prompt_tokens: number; completion_tokens: number;
  cached_tokens: number; reasoning_tokens: number; total_tokens: number;
  cost_microusd: number; average_latency_ms: number; average_ttft_ms: number;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const dashboardToken = typeof window !== "undefined" ? window.sessionStorage.getItem("dashboardToken") : null;
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(dashboardToken ? { "X-Dashboard-Token": dashboardToken } : {}),
      ...(init?.headers || {}),
    },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = (typeof j?.detail === "string" ? j.detail : j?.detail?.message) || j?.message || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  setDashboardToken: (token: string) => window.sessionStorage.setItem("dashboardToken", token.trim()),
  authStatus: () => req<{ auth_mode: string; authenticated: boolean; user: any; has_users: boolean }>("/api/v1/auth/status"),
  me: () => req<{ user: any }>("/api/v1/auth/me"),
  login: (username: string, password: string) =>
    req("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => req("/api/v1/auth/logout", { method: "POST", body: "{}" }),
  bootstrap: (username: string, password: string) =>
    req("/api/v1/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name: "Admin" }),
    }),
  usageSummary: (period = "24h") => req<UsageSummary>(`/api/v1/observability/summary?period=${period}`),
  usageTimeseries: (period = "24h") => req<{ bucket: string; items: any[] }>(`/api/v1/observability/timeseries?period=${period}`),
  providerUsage: (period = "24h") => req<{ items: any[] }>(`/api/v1/observability/providers?period=${period}`),
  quotaSnapshots: () => req<{ items: any[] }>("/api/v1/observability/quota-snapshots/latest"),
  routeExplain: (body: any) => req<any>("/api/v1/observability/route-explain", { method: "POST", body: JSON.stringify(body) }),
  tasks: (page = 1) => req<{ items: any[]; total: number }>(`/api/v1/tasks?page=${page}`),
  task: (id: string) => req<{ task: any; requests: any[] }>(`/api/v1/tasks/${id}`),
  createTask: (body: any) => req("/api/v1/tasks", { method: "POST", body: JSON.stringify(body) }),
  finishTask: (id: string) => req(`/api/v1/tasks/${id}/finish`, { method: "POST", body: "{}" }),
  requests: (page = 1) => req<{ items: any[]; total: number }>(`/api/v1/requests?page=${page}`),
  request: (id: string) => req<{ request: any; attempts: any[] }>(`/api/v1/requests/${id}`),
  keys: () => req<{ items: any[] }>("/api/v1/api-keys"),
  createKey: (body: any) => req("/api/v1/api-keys", { method: "POST", body: JSON.stringify(body) }),
  revokeKey: (id: string) => req(`/api/v1/api-keys/${id}/revoke`, { method: "POST", body: "{}" }),
  stats: () => req<any>("/api/v1/system/stats"),
  health: () => req<any>("/api/v1/system/health"),
  pricing: () => req<{ items: any[] }>("/api/v1/pricing"),
  audit: (page = 1) => req<{ items: any[]; total: number }>(`/api/v1/audit?page=${page}`),
  users: () => req<{ items: any[] }>("/api/v1/users"),
  createUser: (body: any) => req("/api/v1/users", { method: "POST", body: JSON.stringify(body) }),
  disableUser: (id: string) => req(`/api/v1/users/${encodeURIComponent(id)}/disable`, { method: "POST", body: "{}" }),
  createPricing: (body: any) => req("/api/v1/pricing", { method: "POST", body: JSON.stringify(body) }),
  syncPricing: () => req<any>("/api/v1/pricing/sync-litellm", { method: "POST", body: "{}" }),
  jiyi: () => req<any>("/api/v1/system/jiyi"),
  syncJiyi: () => req<any>("/api/v1/system/jiyi/save", { method: "POST", body: "{}" }),

  channels: () => req<{ channels: any[] }>("/api/channels"),
  channelProbe: (id: string) => req<any>(`/api/channels/${encodeURIComponent(id)}/probe`, { method: "POST", body: "{}" }),
  channelBalance: (id: string) => req<any>(`/api/channels/${encodeURIComponent(id)}/balance`, { method: "POST", body: "{}" }),
  updateChannelKey: (id: string, value: string) => req<any>(`/api/channels/${encodeURIComponent(id)}/key`, { method: "POST", body: JSON.stringify({ value }) }),
  updateChannelPriority: (id: string, priority: number) => req<any>(`/api/channels/${encodeURIComponent(id)}/priority`, { method: "POST", body: JSON.stringify({ priority }) }),
  updateChannelTier: (id: string, tier: string) => req<any>(`/api/channels/${encodeURIComponent(id)}/tier`, { method: "POST", body: JSON.stringify({ tier }) }),
  updateChannelModel: (id: string, model: string) => req<any>(`/api/channels/${encodeURIComponent(id)}/model`, { method: "POST", body: JSON.stringify({ model }) }),
  setChannelOptimal: (id: string, body: any) => req<any>(`/api/channels/${encodeURIComponent(id)}/optimal`, { method: "POST", body: JSON.stringify(body) }),
  clearChannelOptimal: (id: string) => req<any>(`/api/channels/${encodeURIComponent(id)}/optimal`, { method: "DELETE" }),
  deleteChannel: (id: string) => req<any>(`/api/channels/${encodeURIComponent(id)}`, { method: "DELETE" }),
  addCompanyAccount: (id: string) => req<any>(`/api/companies/${encodeURIComponent(id)}/accounts`, { method: "POST", body: "{}" }),
  routingControl: () => req<any>("/api/routing-control"),
  updateRoutingControl: (body: any) => req<any>("/api/routing-control", { method: "PUT", body: JSON.stringify(body) }),
  probeRoutingControl: () => req<any>("/api/routing-control/probe", { method: "POST", body: "{}" }),
  discoverCustomProvider: (body: any) => req<any>("/api/custom-providers/discover", { method: "POST", body: JSON.stringify(body) }),
  parseCustomProvider: (body: any) => req<any>("/api/custom-providers/parse-snippet", { method: "POST", body: JSON.stringify(body) }),
  addCustomProvider: (body: any) => req<any>("/api/custom-providers", { method: "POST", body: JSON.stringify(body) }),
  settings: () => req<any>("/api/settings"),
  updateSettings: (body: any) => req<any>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),

  gatewayKeys: () => req<any>("/api/client-keys"),
  createGatewayKey: (name: string) => req<any>("/api/client-keys", { method: "POST", body: JSON.stringify({ name }) }),
  revealGatewayKey: (id: string) => req<any>(`/api/client-keys/${encodeURIComponent(id)}/reveal`),
  probeGatewayKey: (id: string) => req<any>(`/api/client-keys/${encodeURIComponent(id)}/probe`, { method: "POST", body: "{}" }),
  deleteGatewayKey: (id: string) => req<any>(`/api/client-keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
  raiseGatewayKeyLimits: () => req<any>("/api/client-keys/raise-limits", { method: "POST", body: "{}" }),
};

export function formatCost(microusd: number, estimated = false): string {
  const usd = Number(microusd || 0) / 1_000_000;
  const s = usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 });
  return `${estimated ? "~" : ""}$${s}`;
}

export function formatTokens(tokens: number): string {
  const value = Number(tokens || 0);
  if (!Number.isFinite(value)) return "—";
  if (Math.abs(value) < 1_000_000) {
    return value.toLocaleString();
  }
  return `${(value / 1_000_000).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}M`;
}
