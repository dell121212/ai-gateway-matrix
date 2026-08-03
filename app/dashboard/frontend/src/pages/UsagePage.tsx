import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@appica/ui-react/table";
import { Link } from "react-router-dom";
import { api, formatCost, formatTokens } from "../api";
import { periodTime, resolveQuota } from "../channelQuota";
import PageIntro from "../components/PageIntro";

export default function UsagePage() {
  const [period, setPeriod] = useState("24h");
  const summary = useQuery({ queryKey: ["usage-summary", period], queryFn: () => api.usageSummary(period) });
  const providers = useQuery({ queryKey: ["provider-usage", period], queryFn: () => api.providerUsage(period) });
  const timeline = useQuery({ queryKey: ["usage-timeseries", period], queryFn: () => api.usageTimeseries(period) });
  const channels = useQuery({ queryKey: ["channels"], queryFn: api.channels, refetchInterval: 15000 });
  const data = summary.data;
  const configured = (channels.data?.channels || []).filter((item: any) => item.is_configured).sort((a: any, b: any) => String(a.company_name || a.provider_name).localeCompare(String(b.company_name || b.provider_name), "zh-CN") || Number(b.priority || 0) - Number(a.priority || 0));
  const quotaWarnings = configured.filter((item: any) => {
    const remaining = resolveQuota(item).remaining;
    return remaining != null && remaining < 15;
  });
  const trackedWindows = configured.reduce((sum: number, item: any) => sum + (item.rate_limits?.windows || []).filter((window: any) => window.used != null).length, 0);

  return <div className="page-stack">
    <PageIntro title="用量统计" actions={<div className="tier-filter">{["24h", "7d", "30d"].map(value => <Button key={value} size="sm" variant={period === value ? "primary" : "outline"} onClick={() => setPeriod(value)}>{value}</Button>)}</div>} />

    <section className="quiet-metric-grid usage-metrics">
      <article className="quiet-metric"><span>调用</span><strong>{(data?.requests ?? 0).toLocaleString()}</strong><small>{data?.requests ? `成功率 ${((data?.success_rate ?? 0) * 100).toFixed(1)}%` : "暂无调用"}</small></article>
      <article className="quiet-metric"><span>Token</span><strong>{formatTokens(data?.total_tokens ?? 0)}</strong><small>输入 {formatTokens(data?.prompt_tokens ?? 0)} · 输出 {formatTokens(data?.completion_tokens ?? 0)}</small></article>
      <article className="quiet-metric"><span>模型成本</span><strong>{formatCost(data?.cost_microusd ?? 0)}</strong><small>仅统计，不使用积分账本</small></article>
      <article className="quiet-metric"><span>平均响应</span><strong>{data?.average_latency_ms ?? 0} ms</strong><small>TTFT {data?.average_ttft_ms ?? 0} ms</small></article>
    </section>

    <section className="dashboard-section">
      <div className="section-heading"><h3>厂商模型额度</h3><Button variant="outline" size="sm" render={<Link to="/channels" />}>管理渠道与智脑</Button></div>
      <div className={`quota-signal card${quotaWarnings.length ? " quota-signal-warning" : ""}`}>
        <div><span className={`service-dot${quotaWarnings.length ? "" : " service-dot-ok"}`} /><div><strong>{quotaWarnings.length ? `${quotaWarnings.length} 个模型额度低于 15%` : `${configured.length} 个模型 · ${trackedWindows} 个周期窗口正常`}</strong></div></div>
      </div>
      <div className="vendor-quota-grid">
        {configured.map((channel: any) => {
          const quota = resolveQuota(channel);
          const remaining = quota.remaining == null || !Number.isFinite(quota.remaining) ? null : Math.max(0, Math.min(100, quota.remaining));
          const warning = remaining != null && remaining < 15;
          return <article className={`vendor-quota-card card${warning ? " vendor-quota-warning" : ""}`} key={channel.channel_id}>
            <header><div><strong>{channel.company_name || channel.provider_name}</strong><code>{channel.model_display || channel.model}</code></div><Badge variant={warning ? "warning" : "primary-outline"}>{channel.tier || "未分档"}</Badge></header>
            <div className="vendor-quota-summary"><div><span>总消耗 Token</span><strong>{quota.totalTokens == null ? "—" : formatTokens(Number(quota.totalTokens))}</strong></div><div className={warning ? "quota-compact-warning" : ""}><span>当前周期余量</span><strong>{remaining == null ? "未提供" : `${Number(remaining.toFixed(1))}%`}</strong></div><div><span>更新周期</span><strong>{quota.label}</strong><b>{periodTime(quota.seconds, quota.windowSeconds)}</b></div></div>
            <div className={`quota-progress${warning ? " quota-progress-warning" : ""}${remaining == null ? " quota-progress-unknown" : ""}`} role="progressbar" aria-label="当前周期余量" aria-valuemin={0} aria-valuemax={100} aria-valuenow={remaining ?? undefined}><i style={{ width: `${remaining ?? 0}%` }} /></div>
          </article>;
        })}
      </div>
      {!channels.isLoading && !configured.length ? <div className="quiet-empty">尚未配置厂商 Key。前往“渠道与额度”填入免费额度 Key 后，独立看板会自动出现。</div> : null}
    </section>

    <section className="dashboard-section">
      <div className="section-heading"><h3>实际调用分布</h3><span className="section-meta">按选中时间范围统计</span></div>
      <div className="provider-usage-grid">
        {(providers.data?.items || []).map((item: any) => <article className="card provider-usage-card" key={item.provider}><div><strong>{item.provider}</strong><Badge variant="secondary">{item.requests} 次</Badge></div><span>{formatTokens(item.tokens)} Token</span><small>{formatCost(item.cost_microusd)} · {item.average_latency_ms} ms</small></article>)}
      </div>
      {!providers.data?.items?.length ? <div className="quiet-empty">首次调用后，这里会自动显示实际使用的渠道分布。</div> : null}
    </section>

    <details className="advanced-disclosure card">
      <summary>查看完整调用明细</summary>
      <div className="detail-table-stack">
        <div className="table-surface"><Table size="sm" borderStyle="none" hoverableRows><TableHeader><TableRow><TableHead>Provider</TableHead><TableHead>调用</TableHead><TableHead>Token</TableHead><TableHead>成本</TableHead><TableHead>平均延迟</TableHead></TableRow></TableHeader><TableBody>{(providers.data?.items || []).map((item: any) => <TableRow key={item.provider}><TableCell>{item.provider}</TableCell><TableCell>{item.requests}</TableCell><TableCell>{formatTokens(item.tokens)}</TableCell><TableCell>{formatCost(item.cost_microusd)}</TableCell><TableCell>{item.average_latency_ms} ms</TableCell></TableRow>)}</TableBody></Table></div>
        <div className="table-surface"><Table size="sm" borderStyle="none"><TableHeader><TableRow><TableHead>{timeline.data?.bucket === "day" ? "日期" : "小时"}</TableHead><TableHead>Provider</TableHead><TableHead>模型</TableHead><TableHead>调用</TableHead><TableHead>Token</TableHead><TableHead>成本</TableHead><TableHead>延迟</TableHead></TableRow></TableHeader><TableBody>{(timeline.data?.items || []).map((item: any, index: number) => <TableRow key={`${item.bucket}-${item.provider}-${index}`}><TableCell className="muted">{item.bucket}</TableCell><TableCell>{item.provider}</TableCell><TableCell>{item.model || "—"}</TableCell><TableCell>{item.requests}</TableCell><TableCell>{formatTokens(item.total_tokens)}</TableCell><TableCell>{formatCost(item.cost_microusd)}</TableCell><TableCell>{item.average_latency_ms} ms</TableCell></TableRow>)}</TableBody></Table></div>
      </div>
    </details>
  </div>;
}
