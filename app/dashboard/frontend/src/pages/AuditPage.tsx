import { useQuery } from "@tanstack/react-query";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@appica/ui-react/table";
import { api } from "../api";
import PageIntro from "../components/PageIntro";

export default function AuditPage() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api.audit(1) });
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Audit trail"
        title="审计日志"
        description="按时间追踪关键动作、资源与完整上下文。"
      />
      <div className="card table-surface">
        <Table size="sm" borderStyle="none" hoverableRows>
          <TableHeader>
            <TableRow>
              <TableHead>时间</TableHead>
              <TableHead>动作</TableHead>
              <TableHead>资源</TableHead>
              <TableHead>详情</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(q.data?.items || []).map((item: any) => (
              <TableRow key={item.id}>
                <TableCell className="muted">{item.created_at}</TableCell>
                <TableCell>{item.action}</TableCell>
                <TableCell>{item.resource_type} {item.resource_id || ""}</TableCell>
                <TableCell><code>{JSON.stringify(item.detail || {})}</code></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
