import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Separator } from "@appica/ui-react/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@appica/ui-react/table";
import { Link, useParams } from "react-router-dom";
import { api, formatCost, formatTokens } from "../api";
import PageIntro from "../components/PageIntro";

type LiveEvent = {
  event?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost_microusd?: number;
};

export default function TaskDetailPage() {
  const qc = useQueryClient();
  const { id = "" } = useParams();
  const task = useQuery({ queryKey: ["task", id], queryFn: () => api.task(id), refetchInterval: 5000 });
  const finish = useMutation({ mutationFn: () => api.finishTask(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["task", id] }) });
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!id) return;
    const source = new EventSource(`/api/v1/live/tasks/${id}/events`, { withCredentials: true } as any);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    const onMessage = (message: MessageEvent) => {
      try {
        const data = JSON.parse(message.data);
        setEvents((previous) => [data, ...previous].slice(0, 50));
      } catch {
        /* Ignore invalid SSE payloads. */
      }
    };
    source.addEventListener("usage.estimated", onMessage);
    source.addEventListener("usage.observed", onMessage);
    source.addEventListener("usage.started", onMessage);
    source.onmessage = onMessage;
    return () => source.close();
  }, [id]);

  const data = task.data?.task;
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Task inspector"
        title="任务详情"
        description={`任务 ${id ? `${id.slice(0, 12)}…` : "—"} 的调用、成本与实时事件。`}
        actions={<><Badge variant={connected ? "success" : "warning"}>{connected ? "SSE 已连接" : "SSE 重连中"}</Badge>{task.data?.task?.status === "running" ? <Button variant="outline" disabled={finish.isPending} onClick={() => finish.mutate()}>{finish.isPending ? "结束中…" : "结束任务"}</Button> : null}</>}
      />
      {!data ? <div className="card"><p className="muted">加载任务信息中…</p></div> : (
        <>
          <section className="task-summary-grid">
            <article className="card task-summary-card"><span>状态</span><strong>{data.status}</strong></article>
            <article className="card task-summary-card"><span>Token</span><strong>{formatTokens(data.total_tokens || 0)}</strong></article>
            <article className="card task-summary-card"><span>模型成本</span><strong>{formatCost(data.cost_microusd || 0)}</strong></article>
          </section>
          <section className="card event-panel">
            <div className="event-panel-head"><div><p className="section-kicker">Event stream</p><h3>实时事件</h3></div><Badge variant={connected ? "success" : "warning"}>{events.length} 条</Badge></div>
            <Separator />
            <div className="event-list">
              {events.map((event, index) => (
                <div className="event-row" key={index}>
                  <span className="pulse-indicator" />
                  <strong>{event.event || "event"}</strong>
                  {event.prompt_tokens != null ? <Badge variant="info">{formatTokens(Number(event.prompt_tokens || 0) + Number(event.completion_tokens || 0))} tokens</Badge> : null}
                  {event.cost_microusd != null ? <Badge variant="success">{formatCost(event.cost_microusd)}</Badge> : null}
                </div>
              ))}
              {!events.length ? <p className="muted">等待后台事件…</p> : null}
            </div>
          </section>
          <div className="card table-surface">
            <Table size="sm" borderStyle="none" hoverableRows>
              <TableHeader><TableRow><TableHead>请求</TableHead><TableHead>模式</TableHead><TableHead>模型</TableHead><TableHead>状态</TableHead><TableHead>Token</TableHead><TableHead>成本</TableHead></TableRow></TableHeader>
              <TableBody>
                {(task.data?.requests || []).map((request: any) => (
                  <TableRow key={request.id}>
                    <TableCell><Link to={`/requests/${request.id}`}>{request.id.slice(0, 8)}…</Link></TableCell><TableCell>{request.mode}</TableCell><TableCell>{request.requested_model}</TableCell><TableCell>{request.status}</TableCell><TableCell>{formatTokens(Number(request.final_prompt_tokens || 0) + Number(request.final_completion_tokens || 0))}</TableCell><TableCell>{formatCost(request.cost_microusd || 0)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </div>
  );
}
