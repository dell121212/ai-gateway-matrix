import { useQuery } from "@tanstack/react-query";
import { api, formatCredits } from "../api";

export default function LedgerPage() {
  const q = useQuery({ queryKey: ["ledger"], queryFn: () => api.ledger(1) });
  const acc = useQuery({ queryKey: ["account"], queryFn: api.account });
  return (
    <div>
      <h2>积分与账本</h2>
      <div className="card">
        <p>
          余额 <strong>{acc.data ? formatCredits(acc.data.balance_microcredits) : "—"}</strong> ·
          冻结 {acc.data ? formatCredits(acc.data.reserved_microcredits) : "—"}
        </p>
        <p className="muted">账本只追加；纠错使用反向交易，不修改历史流水。</p>
      </div>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>类型</th>
              <th>变动</th>
              <th>余额后</th>
              <th>冻结后</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {(q.data?.items || []).map((e: any) => (
              <tr key={e.id}>
                <td className="muted">{e.created_at}</td>
                <td>{e.transaction_type}</td>
                <td>{formatCredits(e.delta_microcredits)}</td>
                <td>{formatCredits(e.balance_after_microcredits)}</td>
                <td>{formatCredits(e.reserved_after_microcredits)}</td>
                <td>{e.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
