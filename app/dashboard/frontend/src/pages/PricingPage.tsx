import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export default function PricingPage() {
  const q = useQuery({ queryKey: ["pricing"], queryFn: api.pricing });
  return (
    <div>
      <h2>模型与定价</h2>
      <p className="muted">价格单位：microusd / 1M tokens。历史账单绑定价格版本，修改不回溯。</p>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>模型模式</th>
              <th>输入价</th>
              <th>输出价</th>
              <th>计费基础</th>
              <th>倍率</th>
              <th>版本</th>
            </tr>
          </thead>
          <tbody>
            {(q.data?.items || []).map((p: any) => (
              <tr key={p.id}>
                <td>{p.provider}</td>
                <td>{p.model_pattern}</td>
                <td>{p.input_price}</td>
                <td>{p.output_price}</td>
                <td>{p.billing_basis}</td>
                <td>{p.credit_multiplier}</td>
                <td>{p.version}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
