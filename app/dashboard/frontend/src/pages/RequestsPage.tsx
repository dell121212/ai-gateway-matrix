import { useQuery } from "@tanstack/react-query";
import { api, formatCredits } from "../api";

export default function RequestsPage() {
  const q = useQuery({ queryKey: ["requests"], queryFn: () => api.requests(1) });
  return (
    <div>
      <h2>请求明细</h2>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>模式</th>
              <th>模型</th>
              <th>流式</th>
              <th>状态</th>
              <th>估算</th>
              <th>结算</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            {(q.data?.items || []).map((r: any) => (
              <tr key={r.id}>
                <td>{r.id.slice(0, 8)}…</td>
                <td>{r.mode}</td>
                <td>{r.requested_model}</td>
                <td>{String(r.stream)}</td>
                <td>{r.status}</td>
                <td className="badge est">{formatCredits(r.estimated_microcredits, true)}</td>
                <td className="badge settled">{formatCredits(r.settled_microcredits)}</td>
                <td className="muted">{r.settlement_source || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
