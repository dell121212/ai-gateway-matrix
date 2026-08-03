import { useQuery } from "@tanstack/react-query";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { CopyButton } from "@appica/ui-react/copy-button";
import { Skeleton } from "@appica/ui-react/skeleton";
import { Link } from "react-router-dom";
import { api, formatCost, formatTokens } from "../api";
import { resolveQuota } from "../channelQuota";
import PageIntro from "../components/PageIntro";

function Metric({ label, value, note, loading }: { label: string; value: string; note: string; loading?: boolean }) {
  return <article className="quiet-metric"><span>{label}</span>{loading ? <Skeleton className="quota-value-skeleton" /> : <strong>{value}</strong>}<small>{note}</small></article>;
}

const tiers = [
  ["弱", "简单问答", "fast-pool"],
  ["中", "日常任务", "free-pool"],
  ["强", "复杂推理", "strong-model-pool"],
  ["顶级", "高难任务", "elite-model-pool"],
] as const;

export default function OverviewPage() {
  const summary = useQuery({ queryKey: ["usage-summary", "24h"], queryFn: () => api.usageSummary("24h"), refetchInterval: 5000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 5000 });
  const channels = useQuery({ queryKey: ["channels"], queryFn: api.channels, refetchInterval: 15000 });
  const routing = useQuery({ queryKey: ["routing-control"], queryFn: api.routingControl, refetchInterval: 15000 });
  const keys = useQuery({ queryKey: ["gateway-keys"], queryFn: api.gatewayKeys });

  const healthy = Boolean(health.data?.postgres && health.data?.redis);
  const requestCount = summary.data?.requests ?? 0;
  const successRate = summary.data?.success_rate ?? 0;
  const keyCount = keys.data?.keys?.length ?? 0;
  const items = channels.data?.channels || [];
  const configured = items.filter((item: any) => item.is_configured);
  const companies = new Set(configured.map((item: any) => item.company_id || item.company_name)).size;
  const trackedWindows = configured.reduce((sum: number, item: any) => sum + (item.rate_limits?.windows || []).filter((window: any) => window.used != null).length, 0);
  const quotaWarnings = configured.filter((item: any) => {
    const remaining = resolveQuota(item).remaining;
    return remaining != null && remaining < 15;
  });
  const needsAttention = !healthy || keyCount === 0 || quotaWarnings.length > 0;
  const brainLabel = routing.data?.active_label || (routing.isLoading ? "正在读取" : "自动选择");

  return <div className="page-stack overview-page">
    <PageIntro title="智脑总览" />

    <section className={`intelligence-hero${needsAttention ? " intelligence-hero-warning" : ""}`}>
      <div className="intelligence-mark" aria-hidden="true">智</div>
      <div className="intelligence-copy">
        <div className="intelligence-title"><Badge variant={healthy ? "success" : "warning"}>{healthy ? "智脑在线" : "服务需检查"}</Badge><span>{brainLabel}</span></div>
        <h3>{healthy ? "先判断难度，再选择最佳回答模型" : "智脑依赖的基础服务需要检查"}</h3>
        <p>智脑把请求分为弱、中、强、顶级四档；档内按你的配置顺序选择，并自动跳过额度不足、冷却或能力不匹配的模型，答检不合格会换路。</p>
      </div>
      <Button render={<Link to={healthy ? "/channels" : "/health"} />}>{healthy ? "管理智脑与额度" : "查看健康状态"}</Button>
    </section>

    <section className="brain-flow card" aria-label="智脑路由流程">
      <div className="brain-flow-step"><span>1</span><strong>收到请求</strong><small>统一 Key · auto-route</small></div>
      <i aria-hidden="true">→</i>
      <div className="brain-flow-step brain-flow-core"><span>2</span><strong>智脑判断</strong><small>难度与能力</small></div>
      <i aria-hidden="true">→</i>
      <div className="brain-flow-step"><span>3</span><strong>进入档位</strong><small>弱 / 中 / 强 / 顶级</small></div>
      <i aria-hidden="true">→</i>
      <div className="brain-flow-step"><span>4</span><strong>最佳可用模型</strong><small>额度 · 冷却 · 答检</small></div>
    </section>

    <section className="tier-route-grid">
      {tiers.map(([tier, purpose]) => {
        const count = configured.filter((item: any) => item.tier === tier).length;
        return <article className="tier-route-card card" key={tier}><div><Badge variant="soft">{tier}</Badge><strong>{count}</strong></div><p>{purpose}</p><small>{count ? `${count} 个已配置模型参与路由` : "尚未配置可用模型"}</small></article>;
      })}
    </section>

    <section className="quick-connect card">
      <div><span className="muted">统一地址</span><code>http://127.0.0.1:4000/v1</code></div>
      <CopyButton value="http://127.0.0.1:4000/v1" label="复制地址" copiedLabel="已复制" size="sm" />
      <div><span className="muted">智能路由模型</span><code>auto-route</code></div>
      <CopyButton value="auto-route" label="复制模型" copiedLabel="已复制" size="sm" />
    </section>

    <section className="dashboard-section">
      <div className="section-heading"><h3>最近 24 小时</h3><span className="section-meta">真实调用统计 · 自动更新</span></div>
      <div className="quiet-metric-grid">
        <Metric label="调用" value={requestCount.toLocaleString()} note={requestCount ? `成功率 ${(successRate * 100).toFixed(1)}%` : "等待首次调用"} loading={summary.isLoading} />
        <Metric label="Token" value={formatTokens(summary.data?.total_tokens ?? 0)} note={`缓存 ${formatTokens(summary.data?.cached_tokens ?? 0)}`} loading={summary.isLoading} />
        <Metric label="模型成本" value={formatCost(summary.data?.cost_microusd ?? 0)} note="统计值，不使用积分账本" loading={summary.isLoading} />
        <Metric label="平均响应" value={`${summary.data?.average_latency_ms ?? 0} ms`} note={`TTFT ${summary.data?.average_ttft_ms ?? 0} ms`} loading={summary.isLoading} />
      </div>
    </section>

    <section className="smart-facts">
      <article><span className={`service-dot${healthy ? " service-dot-ok" : ""}`} /><div><strong>{healthy ? "服务正常" : "服务异常"}</strong><small>PostgreSQL 与 Redis</small></div></article>
      <article><span className="smart-fact-value">{configured.length}</span><div><strong>已配置模型</strong><small>{companies} 个厂商账号</small></div></article>
      <article><span className={`smart-fact-value${quotaWarnings.length ? " warning-text" : ""}`}>{quotaWarnings.length}</span><div><strong>额度提醒</strong><small>{trackedWindows} 个窗口正在跟踪</small></div></article>
      <article><span className="smart-fact-value">{keyCount}</span><div><strong>客户端 Key</strong><small>随 jiyi 安全迁移</small></div></article>
    </section>
  </div>;
}
