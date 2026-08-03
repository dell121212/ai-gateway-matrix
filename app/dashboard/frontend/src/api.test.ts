import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

const fetchMock = vi.fn(async () => ({
  ok: true,
  status: 200,
  statusText: "OK",
  json: async () => ({ ok: true }),
}));

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

function call(index: number) {
  const [path, init] = fetchMock.mock.calls[index] as unknown as [string, RequestInit];
  return { path, method: init?.method || "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined };
}

describe("Appica console API bindings", () => {
  it("binds every channel and routing mutation to the existing classic contracts", async () => {
    await api.updateChannelKey("ch/1", "secret");
    await api.updateChannelPriority("ch/1", 88);
    await api.updateChannelTier("ch/1", "strong-model-pool");
    await api.updateChannelModel("ch/1", "model-x");
    await api.channelProbe("ch/1");
    await api.channelBalance("ch/1");
    await api.setChannelOptimal("ch/1", { reason: "trial", expires_in_hours: 2 });
    await api.clearChannelOptimal("ch/1");
    await api.addCompanyAccount("company/1");
    await api.updateRoutingControl({ mode: "auto", answer_verify_mode: "hybrid" });
    await api.probeRoutingControl();
    await api.addCustomProvider({ provider_name: "P", api_base: "https://p.test/v1", api_key: "k", model: "m" });
    await api.deleteChannel("ch/1");

    expect(call(0)).toEqual({ path: "/api/channels/ch%2F1/key", method: "POST", body: { value: "secret" } });
    expect(call(1).body).toEqual({ priority: 88 });
    expect(call(2).body).toEqual({ tier: "strong-model-pool" });
    expect(call(3).body).toEqual({ model: "model-x" });
    expect(call(4).path).toBe("/api/channels/ch%2F1/probe");
    expect(call(5).path).toBe("/api/channels/ch%2F1/balance");
    expect(call(6).body).toEqual({ reason: "trial", expires_in_hours: 2 });
    expect(call(7).method).toBe("DELETE");
    expect(call(8).path).toBe("/api/companies/company%2F1/accounts");
    expect(call(9)).toEqual({ path: "/api/routing-control", method: "PUT", body: { mode: "auto", answer_verify_mode: "hybrid" } });
    expect(call(10)).toEqual({ path: "/api/routing-control/probe", method: "POST", body: {} });
    expect(call(11).path).toBe("/api/custom-providers");
    expect(call(12)).toEqual({ path: "/api/channels/ch%2F1", method: "DELETE", body: undefined });
  });

  it("binds task, user, pricing, route explanation and memory write operations", async () => {
    await api.createTask({ title: "task" });
    await api.finishTask("task-id");
    await api.createUser({ username: "new-user", password: "strong-pass-1" });
    await api.disableUser("user-id");
    await api.routeExplain({ strategy: "headroom", candidates: [] });
    await api.createPricing({ provider: "*", model_pattern: "m*" });
    await api.syncJiyi();
    await api.updateSettings({ theme: "dark", autostart: true });

    expect(call(0).path).toBe("/api/v1/tasks");
    expect(call(1).path).toBe("/api/v1/tasks/task-id/finish");
    expect(call(2).path).toBe("/api/v1/users");
    expect(call(3).path).toBe("/api/v1/users/user-id/disable");
    expect(call(4).path).toBe("/api/v1/observability/route-explain");
    expect(call(5).path).toBe("/api/v1/pricing");
    expect(call(6).path).toBe("/api/v1/system/jiyi/save");
    expect(call(7)).toEqual({ path: "/api/settings", method: "PUT", body: { theme: "dark", autostart: true } });
  });

  it("binds the complete gateway client key lifecycle", async () => {
    await api.createGatewayKey("client");
    await api.revealGatewayKey("key/id");
    await api.probeGatewayKey("key/id");
    await api.raiseGatewayKeyLimits();
    await api.deleteGatewayKey("key/id");

    expect(call(0)).toEqual({ path: "/api/client-keys", method: "POST", body: { name: "client" } });
    expect(call(1).path).toBe("/api/client-keys/key%2Fid/reveal");
    expect(call(2).path).toBe("/api/client-keys/key%2Fid/probe");
    expect(call(3).path).toBe("/api/client-keys/raise-limits");
    expect(call(4)).toEqual({ path: "/api/client-keys/key%2Fid", method: "DELETE", body: undefined });
  });
});
