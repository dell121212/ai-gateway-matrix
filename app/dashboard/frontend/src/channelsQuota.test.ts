import { describe, expect, it } from "vitest";
import { periodTime, resolveQuota } from "./channelQuota";

describe("channel quota parity", () => {
  it("keeps the classic quota summary fields", () => {
    const quota = resolveQuota({
      channel_id: "groq-main",
      usage: { total_tokens: 123456, day_tokens: 321 },
      rate_limits: {
        summary: {
          period_label: "每日请求 (RPD)",
          remaining_pct: 72.5,
          used: 55,
          limit: 200,
          seconds_until_reset: 3661,
        },
        windows: [],
      },
    });

    expect(quota).toMatchObject({
      remaining: 72.5,
      used: 55,
      limit: 200,
      label: "每日请求",
      seconds: 3661,
      todayTokens: 321,
      totalTokens: 123456,
    });
    expect(periodTime(quota.seconds, quota.windowSeconds)).toBe("1 小时 1 分");
  });

  it("falls back to the preferred daily window when summary is absent", () => {
    const quota = resolveQuota({
      channel_id: "fallback",
      rate_limits: {
        windows: [
          { id: "rpm", label_zh: "每分钟请求 (RPM)", used: 1, limit: 30, window_sec: 60 },
          { id: "rpd", label_zh: "每日请求 (RPD)", used: 55, limit: 200, window_sec: 86400 },
        ],
      },
    });

    expect(quota.label).toBe("每日请求");
    expect(quota.used).toBe(55);
    expect(quota.limit).toBe(200);
    expect(quota.remaining).toBe(72.5);
  });
});
