import { useQuery } from "@tanstack/react-query";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@appica/ui-react/table";
import { Link, useParams } from "react-router-dom";
import { api, formatCost, formatTokens } from "../api";
import PageIntro from "../components/PageIntro";

export default function RequestDetailPage() {
  const { id = "" } = useParams();
  const query = useQuery({ queryKey: ["request", id], queryFn: () => api.request(id), enabled: Boolean(id) });
  const request = query.data?.request || {};
  return <div className="page-stack">
    <PageIntro eyebrow="Request trace" title={`请求 ${id.slice(0, 8)}…`} description="查看路由决策、每次上游尝试和最终调用结果。" actions={<Button variant="outline" render={<Link to="/requests" />}>返回请求列表</Button>} />
    <section className="request-summary-grid">{[["状态", request.status], ["Provider", request.provider], ["请求 / 实际模型", `${request.requested_model || "—"} / ${request.actual_model || "—"}`], ["Token", formatTokens(Number(request.final_prompt_tokens || 0) + Number(request.final_completion_tokens || 0))], ["成本", formatCost(request.cost_microusd || 0)], ["延迟 / TTFT", `${request.latency_ms ?? "—"} / ${request.ttft_ms ?? "—"} ms`], ["路由策略", request.route_strategy], ["路由理由", request.route_reason]].map(([label, value]) => <article className="card request-summary-card" key={label}><span>{label}</span><strong>{value || "—"}</strong></article>)}</section>
    <div className="card table-surface"><Table size="sm" borderStyle="none" hoverableRows><TableHeader><TableRow><TableHead>#</TableHead><TableHead>Provider</TableHead><TableHead>实际模型</TableHead><TableHead>状态</TableHead><TableHead>Token</TableHead><TableHead>成本</TableHead><TableHead>延迟 / TTFT</TableHead><TableHead>错误</TableHead></TableRow></TableHeader><TableBody>{(query.data?.attempts || []).map((attempt: any) => <TableRow key={attempt.id} highlighted={attempt.is_final_success}><TableCell>{attempt.attempt_number}</TableCell><TableCell>{attempt.provider || "—"}</TableCell><TableCell>{attempt.actual_model}</TableCell><TableCell><Badge variant={attempt.is_final_success ? "success" : attempt.status === "failed" ? "error" : "secondary"}>{attempt.status}</Badge></TableCell><TableCell>{formatTokens(Number(attempt.prompt_tokens || 0) + Number(attempt.completion_tokens || 0))}</TableCell><TableCell>{formatCost(attempt.cost_microusd || 0)}</TableCell><TableCell>{attempt.latency_ms ?? "—"} / {attempt.ttft_ms ?? "—"} ms</TableCell><TableCell className="muted">{attempt.quality_failure_reason || attempt.error_class || "—"}</TableCell></TableRow>)}</TableBody></Table>{!query.data?.attempts?.length ? <p className="table-empty">暂无上游尝试记录。</p> : null}</div>
  </div>;
}
