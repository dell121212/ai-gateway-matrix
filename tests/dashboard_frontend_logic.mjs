import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync(new URL("../dashboard/static/index.html", import.meta.url), "utf8");
if (!html.includes('id="apiBaseValue"') || !html.includes("AI API 控制台")) {
  throw new Error("Chinese unified API console is missing");
}
const start = html.indexOf("<script>") + "<script>".length;
const end = html.lastIndexOf("</script>");
if (start < "<script>".length || end < start) throw new Error("dashboard script not found");

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      innerHTML: "",
      textContent: "",
      value: "",
      dataset: {},
      listeners: {},
      classList: { add() {}, remove() {} },
      addEventListener(type, listener) { this.listeners[type] = listener; },
      focus() {},
      select() {},
    });
  }
  return elements.get(id);
}

const storage = new Map();
const sandbox = {
  console,
  Headers,
  document: {
    getElementById: element,
    querySelectorAll() { return []; },
  },
  window: {
    prompt() { return ""; },
    location: { protocol: "http:", hostname: "127.0.0.1" },
  },
  navigator: { clipboard: { async writeText() {} } },
  sessionStorage: {
    getItem(key) { return storage.get(key) || null; },
    setItem(key, value) { storage.set(key, value); },
  },
  fetch: async () => ({ ok: true, status: 200, json: async () => ({ channels: [] }) }),
  setInterval() { return 0; },
  setTimeout() { return 0; },
  clearTimeout() {},
};
vm.createContext(sandbox);
vm.runInContext(html.slice(start, end), sandbox);

vm.runInContext(`
  const configured = {
    provider_name: "Configured", model: "openai/a", tier: "强",
    is_configured: true, is_optimal: false, priority: 1,
  };
  const unconfigured = {
    provider_name: "Unconfigured", model: "openai/b", tier: "强",
    is_configured: false, is_optimal: true, priority: 999,
  };
  const sorted = [unconfigured, configured].sort(channelOrder);
  if (sorted[0] !== configured) throw new Error("configured channel was not pinned first");

  const grouped = groupChannelsByProvider([
    {...configured, channel_id: "a", provider_name: "One Co"},
    {...configured, channel_id: "b", provider_name: "One Co", model: "openai/b"},
    {...configured, channel_id: "c", provider_name: "Two Co"},
  ], "强");
  if (grouped.length !== 2 || grouped[0].items.length !== 2) {
    throw new Error("provider cards were not grouped for model dropdowns");
  }

  searchQuery = "general compute";
  if (!matchesSearch({provider_name: "General Compute", model: "openai/gpt-oss-120b"})) {
    throw new Error("provider search did not match");
  }
  searchQuery = "gpt-oss";
  if (!matchesSearch({provider_name: "General Compute", model: "openai/gpt-oss-120b"})) {
    throw new Error("model search did not match");
  }

  collapsedTiers.clear();
  toggleTier("强");
  if (!collapsedTiers.has("强")) throw new Error("single tier did not collapse");
  toggleTier("强");
  if (collapsedTiers.has("强")) throw new Error("single tier did not expand");
  toggleAllTiers();
  if (!TIER_ORDER.every(tier => collapsedTiers.has(tier))) throw new Error("collapse all failed");
  toggleAllTiers();
  if (collapsedTiers.size !== 0) throw new Error("expand all failed");
`, sandbox);

console.log("dashboard search/collapse/configured-first logic: PASS");
