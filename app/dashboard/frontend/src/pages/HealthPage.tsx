import { useQuery } from "@tanstack/react-query";
import { Badge } from "@appica/ui-react/badge";
import { Separator } from "@appica/ui-react/separator";
import { api } from "../api";
import PageIntro from "../components/PageIntro";

export default function HealthPage() {
  const q = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 5000 });
  const data = q.data;
  const healthy = Boolean(data?.postgres && data?.redis);
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="System telemetry"
        title="系统健康"
        description="每 5 秒重新检查数据库、缓存和网关版本信息。"
        actions={<Badge variant={healthy ? "success" : "warning"}>{q.isLoading ? "检查中" : healthy ? "运行正常" : "需要检查"}</Badge>}
      />
      <section className="health-grid">
        <article className="card health-service-card">
          <span className={`health-service-orb${data?.postgres ? " health-service-orb-ok" : ""}`} />
          <div><span>Postgres</span><strong>{data?.postgres ? "在线" : "离线"}</strong></div>
        </article>
        <article className="card health-service-card">
          <span className={`health-service-orb${data?.redis ? " health-service-orb-ok" : ""}`} />
          <div><span>Redis</span><strong>{data?.redis ? "在线" : "离线"}</strong></div>
        </article>
        <article className="card health-service-card">
          <span className="health-version-mark">V</span>
          <div><span>当前版本</span><strong>{data?.version ?? "—"}</strong></div>
        </article>
      </section>
      <section className="card health-raw-card">
        <div className="health-raw-head"><div><p className="section-kicker">Raw response</p><h3>完整健康数据</h3></div><Badge variant="outline" size="xs">JSON</Badge></div>
        <Separator />
        <pre>{JSON.stringify(data, null, 2)}</pre>
      </section>
    </div>
  );
}
