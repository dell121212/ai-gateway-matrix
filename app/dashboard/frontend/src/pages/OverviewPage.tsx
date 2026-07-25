import { useQuery } from "@tanstack/react-query";
import { api, formatCredits } from "../api";

export default function OverviewPage() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 5000 });
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });

  return (
    <div>
      <h2>总览</h2>
      <div className="grid">
        <div className="card stat">
          <div className="label">今日请求</div>
          <div className="value">{stats.data?.today_requests ?? "—"}</div>
        </div>
        <div className="card stat">
          <div className="label">今日已结算积分</div>
          <div className="value">
            {stats.data ? formatCredits(stats.data.today_settled_microcredits) : "—"}
          </div>
        </div>
        <div className="card stat">
          <div className="label">活跃任务</div>
          <div className="value">{stats.data?.active_tasks ?? "—"}</div>
        </div>
        <div className="card stat">
          <div className="label">未结算请求</div>
          <div className="value">{stats.data?.unsettled_requests ?? "—"}</div>
        </div>
        <div className="card stat">
          <div className="label">账户余额</div>
          <div className="value">
            {me.data ? formatCredits(me.data.credits.balance_microcredits) : "—"}
          </div>
        </div>
        <div className="card stat">
          <div className="label">已冻结</div>
          <div className="value">
            {me.data ? formatCredits(me.data.credits.reserved_microcredits) : "—"}
          </div>
        </div>
      </div>
      <div className="card">
        <h3>系统</h3>
        <p className="muted">
          Postgres: {String(health.data?.postgres)} · Redis: {String(health.data?.redis)} ·
          版本 {health.data?.version} · 计费失败模式 {health.data?.billing_fail_mode}
        </p>
        <p className="muted">
          积分动画仅平滑后台真实事件；估算显示为 ~数字，结算后去掉波浪号。
        </p>
      </div>
    </div>
  );
}
