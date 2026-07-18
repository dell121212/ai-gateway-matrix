import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync(new URL("../dashboard/static/index.html", import.meta.url), "utf8");
if (!html.includes('id="apiBaseValue"') || !html.includes("AI API 控制台")) {
  throw new Error("Chinese unified API console is missing");
}
if (!html.includes("累计节省") || html.includes("预计累计花费")) {
  throw new Error("cumulative savings hero metric is missing");
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
      hidden: false,
      dataset: {},
      listeners: {},
      classList: {
        add() {},
        remove() {},
        toggle() { return false; },
      },
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
    querySelector() { return null; },
    addEventListener() {},
    scrollingElement: { scrollTop: 0 },
    documentElement: { scrollTop: 0 },
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
  morphdom: undefined,
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
  const sorted = [configured, unconfigured].sort(channelOrder);
  if (sorted[0] !== unconfigured) throw new Error("manual priority was not the strongest sort key");

  activeTier = "全部";
  const grouped = groupChannelsByCompany([
    {...configured, channel_id: "a", company_id: "low", company_name: "Low", tier: "弱"},
    {...unconfigured, channel_id: "b", company_id: "high", company_name: "High", tier: "顶级"},
    {...unconfigured, channel_id: "c", company_id: "mid", company_name: "Mid", tier: "顶级", priority: 100},
  ]);
  if (grouped[0].company_id !== "low") {
    throw new Error("companies with a higher tier were not sunk");
  }
  if (grouped[1].company_id !== "high" || grouped[2].company_id !== "mid") {
    throw new Error("manual priority was not strongest inside the same sink group");
  }

  if (fmtTokensM(3778) !== "0.0038M" || fmtTokensM(2500000) !== "2.50M") {
    throw new Error("token usage was not formatted in millions");
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

console.log("dashboard search/collapse/priority-first logic: PASS");
