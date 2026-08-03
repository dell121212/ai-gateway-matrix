import { useQuery } from "@tanstack/react-query";
import { Badge } from "@appica/ui-react/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@appica/ui-react/table";
import { api, formatCost, formatTokens } from "../api";
import PageIntro from "../components/PageIntro";
import { Link } from "react-router-dom";

export default function RequestsPage() {
  const q = useQuery({ queryKey: ["requests"], queryFn: () => api.requests(1) });
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Call logs"
        title="调用日志"
        description="查看每次调用的 Provider、模型、Token、成本、延迟与路由结果。"
      />
      <div className="card table-surface">
        <Table size="sm" borderStyle="none" hoverableRows>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead>
              <TableHead>模式</TableHead>
              <TableHead>模型</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>Token</TableHead>
              <TableHead>成本</TableHead>
              <TableHead>延迟</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(q.data?.items || []).map((item: any) => (
              <TableRow key={item.id}>
                <TableCell><Link to={`/requests/${item.id}`}>{item.id.slice(0, 8)}…</Link></TableCell>
                <TableCell>{item.mode}</TableCell>
                <TableCell>{item.requested_model}</TableCell>
                <TableCell>{item.provider || "—"}</TableCell>
                <TableCell>{item.status}</TableCell>
                <TableCell>{formatTokens(Number(item.final_prompt_tokens || 0) + Number(item.final_completion_tokens || 0))}</TableCell>
                <TableCell><Badge variant="success">{formatCost(item.cost_microusd || 0)}</Badge></TableCell>
                <TableCell className="muted">{item.latency_ms != null ? `${item.latency_ms} ms` : "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
