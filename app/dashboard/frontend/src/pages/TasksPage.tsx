import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription } from "@appica/ui-react/alert";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Input } from "@appica/ui-react/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@appica/ui-react/table";
import { Link } from "react-router-dom";
import { api, formatCost, formatTokens } from "../api";
import PageIntro from "../components/PageIntro";

export default function TasksPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["tasks"], queryFn: () => api.tasks(1), refetchInterval: 3000 });
  const [title, setTitle] = useState("");
  const [clientName, setClientName] = useState("manual");
  const create = useMutation({ mutationFn: () => api.createTask({ title, client_name: clientName }), onSuccess: () => { setTitle(""); qc.invalidateQueries({ queryKey: ["tasks"] }); } });
  const finish = useMutation({ mutationFn: api.finishTask, onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }) });
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Live operations"
        title="实时任务中心"
        description="跟踪长任务、调用聚合与实时用量事件。"
      />
      <section className="card compact-create-form">
        <div><h3>创建任务</h3></div>
        <Input aria-label="任务标题" placeholder="任务标题" value={title} onChange={(event) => setTitle(event.target.value)} />
        <Input aria-label="客户端名称" placeholder="客户端" value={clientName} onChange={(event) => setClientName(event.target.value)} />
        <Button disabled={create.isPending || !title.trim()} onClick={() => create.mutate()}>{create.isPending ? "创建中…" : "创建任务"}</Button>
      </section>
      {(create.isError || finish.isError) ? <Alert variant="error"><AlertDescription>{((create.error || finish.error) as Error).message}</AlertDescription></Alert> : null}
      <div className="card table-surface">
        <Table size="sm" borderStyle="none" hoverableRows>
          <TableHeader>
            <TableRow>
              <TableHead>任务</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>客户端</TableHead>
              <TableHead>请求数</TableHead>
              <TableHead>Token</TableHead>
              <TableHead>模型成本</TableHead>
              <TableHead>分组</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(q.data?.items || []).map((item: any) => (
              <TableRow key={item.id}>
                <TableCell><Link to={`/tasks/${item.id}`}>{item.id.slice(0, 8)}…</Link></TableCell>
                <TableCell><Badge variant="secondary">{item.status}</Badge></TableCell>
                <TableCell>{item.client_name || "—"}</TableCell>
                <TableCell>{item.request_count}</TableCell>
                <TableCell><Badge variant="info">{formatTokens(item.total_tokens || 0)}</Badge></TableCell>
                <TableCell><Badge variant="success">{formatCost(item.cost_microusd || 0)}</Badge></TableCell>
                <TableCell className="muted">{item.grouping_source}</TableCell>
                <TableCell>{item.status === "running" ? <Button variant="outline" size="sm" disabled={finish.isPending} onClick={() => finish.mutate(item.id)}>结束任务</Button> : "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {!q.data?.items?.length ? <p className="table-empty">暂无任务。通过 Cline/客户端请求后会出现。</p> : null}
      </div>
    </div>
  );
}
