export type Credits = {
  balance_microcredits: number;
  reserved_microcredits: number;
  available_microcredits?: number;
  balance_credits?: number;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j?.detail?.message || j?.message || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  authStatus: () => req<{ auth_mode: string; authenticated: boolean; user: any; has_users: boolean }>("/api/v1/auth/status"),
  me: () => req<{ user: any; credits: Credits }>("/api/v1/auth/me"),
  login: (username: string, password: string) =>
    req("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => req("/api/v1/auth/logout", { method: "POST", body: "{}" }),
  bootstrap: (username: string, password: string) =>
    req("/api/v1/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name: "Admin" }),
    }),
  account: () => req<Credits & { id: string }>("/api/v1/credit-accounts/me"),
  ledger: (page = 1) => req<{ items: any[]; total: number }>(`/api/v1/credit-ledger?page=${page}`),
  tasks: (page = 1) => req<{ items: any[]; total: number }>(`/api/v1/tasks?page=${page}`),
  task: (id: string) => req<{ task: any; requests: any[] }>(`/api/v1/tasks/${id}`),
  requests: (page = 1) => req<{ items: any[]; total: number }>(`/api/v1/requests?page=${page}`),
  request: (id: string) => req<{ request: any; attempts: any[] }>(`/api/v1/requests/${id}`),
  keys: () => req<{ items: any[] }>("/api/v1/api-keys"),
  createKey: (body: any) => req("/api/v1/api-keys", { method: "POST", body: JSON.stringify(body) }),
  revokeKey: (id: string) => req(`/api/v1/api-keys/${id}/revoke`, { method: "POST", body: "{}" }),
  stats: () => req<any>("/api/v1/system/stats"),
  health: () => req<any>("/api/v1/system/health"),
  pricing: () => req<{ items: any[] }>("/api/v1/pricing"),
  audit: (page = 1) => req<{ items: any[]; total: number }>(`/api/v1/audit?page=${page}`),
  adjust: (body: any) =>
    req("/api/v1/credit-accounts/adjust", { method: "POST", body: JSON.stringify(body) }),
};

export function formatCredits(micro: number, estimated = false): string {
  const c = micro / 1_000_000;
  const s = c.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return estimated ? `~${s}` : s;
}
