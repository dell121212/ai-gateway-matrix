export type QuotaChannel = Record<string, any> & { channel_id: string };

export function periodTime(seconds: unknown, windowSeconds: unknown) {
  const remaining = Number(seconds);
  if (Number.isFinite(remaining) && remaining >= 0) {
    const hours = Math.floor(remaining / 3600);
    const minutes = Math.floor((remaining % 3600) / 60);
    if (hours) return `${hours} 小时 ${minutes} 分`;
    if (minutes) return `${minutes} 分`;
    return `${Math.floor(remaining)} 秒`;
  }
  const window = Number(windowSeconds);
  if (window >= 86400) return `${Math.round(window / 86400)} 天周期`;
  if (window >= 3600) return `${Math.round(window / 3600)} 小时周期`;
  if (window >= 60) return `${Math.round(window / 60)} 分钟周期`;
  return "待返回";
}

export function cleanPeriodLabel(value: unknown) {
  return String(value || "当前周期").replace(/\s*\([^)]*\)\s*/g, "").trim();
}

export function resolveQuota(channel: QuotaChannel) {
  const rateLimits = channel.rate_limits || {};
  const summary = rateLimits.summary || {};
  const windows = Array.isArray(rateLimits.windows) ? rateLimits.windows : [];
  const preferred = ["rpd", "month_tokens", "tpd", "rpm", "tpm"];
  let primary = preferred.map((id) => windows.find((item: any) => item.id === id && Number(item.limit) > 0)).find(Boolean);
  primary ||= windows.find((item: any) => Number(item.limit) > 0);
  const useSummary = summary.remaining_pct != null || Number(summary.limit) > 0;
  const used = useSummary ? summary.used : primary?.used;
  const limit = useSummary ? summary.limit : primary?.limit;
  let remaining = summary.remaining_pct == null ? null : Number(summary.remaining_pct);
  if (remaining == null && used != null && Number(limit) > 0) remaining = Math.max(0, Math.min(100, (1 - Number(used) / Number(limit)) * 100));
  return {
    windows,
    used,
    limit,
    remaining,
    label: cleanPeriodLabel(useSummary ? summary.period_label : primary?.label_zh || primary?.window_label),
    seconds: summary.seconds_until_reset ?? primary?.seconds_until_reset,
    windowSeconds: primary?.window_sec,
    todayTokens: summary.day_tokens ?? channel.usage?.day_tokens,
    totalTokens: summary.total_tokens ?? channel.usage?.total_tokens,
  };
}
