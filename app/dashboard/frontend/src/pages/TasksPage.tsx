import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, formatCredits } from "../api";

export default function TasksPage() {
  const q = useQuery({ queryKey: ["tasks"], queryFn: () => api.tasks(1), refetchInterval: 3000 });
  return (
    <div>
      <h2>实时任务中心</h2>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>状态</th>
              <th>客户端</th>
              <th>请求数</th>
              <th>估算积分</th>
              <th>已结算</th>
              <th>分组</th>
            </tr>
          </thead>
          <tbody>
            {(q.data?.items || []).map((t: any) => (
              <tr key={t.id}>
                <td>
                  <Link to={`/tasks/${t.id}`}>{t.id.slice(0, 8)}…</Link>
                </td>
                <td>
                  <span className="badge">{t.status}</span>
                </td>
                <td>{t.client_name || "—"}</td>
                <td>{t.request_count}</td>
                <td>
                  <span className="badge est">{formatCredits(t.estimated_microcredits, true)}</span>
                </td>
                <td>
                  <span className="badge settled">{formatCredits(t.settled_microcredits)}</span>
                </td>
                <td className="muted">{t.grouping_source}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!q.data?.items?.length && <p className="muted">暂无任务。通过 Cline/客户端请求后会出现。</p>}
      </div>
    </div>
  );
}
