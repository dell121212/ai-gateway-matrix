import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function source(path: string) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

describe("product contract", () => {
  it("keeps channels and vendor quotas as a primary destination", () => {
    const app = source("./App.tsx");

    expect(app).toContain('{ to: "/channels", label: "渠道与额度"');
  });

  it("presents the brain and reads live channel quotas on overview", () => {
    const overview = source("./pages/OverviewPage.tsx");

    expect(overview).toContain("api.routingControl");
    expect(overview).toContain("api.channels");
    expect(overview).toContain("智脑");
    expect(overview).not.toContain("api.quotaSnapshots");
  });

  it("uses each channel's live quota windows on the usage page", () => {
    const usage = source("./pages/UsagePage.tsx");

    expect(usage).toContain("api.channels");
    expect(usage).not.toContain("api.quotaSnapshots");
  });

  it("keeps quota cards compact, three-up, and progress-led", () => {
    const channels = source("./pages/ChannelsPage.tsx");
    const usage = source("./pages/UsagePage.tsx");
    const styles = source("./styles.css");

    expect(styles).toMatch(/\.channel-grid\s*\{[^}]*repeat\(3,/s);
    for (const label of ["总消耗 Token", "当前周期余量", "更新周期"]) {
      expect(channels).toContain(label);
      expect(usage).toContain(label);
    }
    expect(channels).toContain('role="progressbar"');
    expect(usage).toContain('role="progressbar"');
  });

  it("keeps account actions visible and gives the brain a real connection check", () => {
    const channels = source("./pages/ChannelsPage.tsx");
    const editorStart = channels.indexOf('className="channel-details channel-editor"');
    const editorEnd = channels.indexOf("</details>", editorStart);

    expect(channels).toContain("api.probeRoutingControl");
    expect(channels).toContain("检查智脑连接");
    expect(channels.indexOf("新增账号")).toBeGreaterThan(editorEnd);
    expect(channels.indexOf("删除渠道")).toBeGreaterThan(editorEnd);
  });

  it("uses one stable app scroll container in desktop mode", () => {
    const app = source("./App.tsx");
    const main = source("./main.tsx");
    const styles = source("./styles.css");

    expect(main).toContain('classList.toggle("desktop-app"');
    expect(app).toContain('classList.add("dashboard-active")');
    expect(app).toContain("desktopMode ? false");
    expect(styles).toMatch(/body\.dashboard-active\s*\{[^}]*overflow:\s*hidden/s);
    expect(styles).toMatch(/\.workspace\s*\{[^}]*overflow-y:\s*scroll/s);
    expect(styles).toContain("scrollbar-gutter: stable");
  });
});
