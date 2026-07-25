import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export default function HealthPage() {
  const q = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 5000 });
  const d = q.data;
  return (
    <div>
      <h2>系统健康</h2>
      <div className="card">
        <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(d, null, 2)}</pre>
      </div>
    </div>
  );
}
