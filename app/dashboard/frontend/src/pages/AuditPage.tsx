import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export default function AuditPage() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api.audit(1) });
  return (
    <div>
      <h2>审计日志</h2>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>动作</th>
              <th>资源</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            {(q.data?.items || []).map((a: any) => (
              <tr key={a.id}>
                <td className="muted">{a.created_at}</td>
                <td>{a.action}</td>
                <td>
                  {a.resource_type} {a.resource_id || ""}
                </td>
                <td>
                  <code>{JSON.stringify(a.detail || {})}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
