import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export default function KeysPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["keys"], queryFn: api.keys });
  const [alias, setAlias] = useState("cline");
  const [mode, setMode] = useState("agent-stream");
  const [once, setOnce] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.createKey({ alias, default_mode: mode }),
    onSuccess: (data: any) => {
      setOnce(data.api_key);
      qc.invalidateQueries({ queryKey: ["keys"] });
    },
  });

  return (
    <div>
      <h2>用户与 API Key</h2>
      <div className="card">
        <h3>创建客户端 Key</h3>
        <label>别名</label>
        <input value={alias} onChange={(e) => setAlias(e.target.value)} />
        <label>默认模式</label>
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="agent-stream">agent-stream（Cline/Roo 流式）</option>
          <option value="strict">strict（完整质检换家）</option>
        </select>
        <button onClick={() => create.mutate()} disabled={create.isPending}>
          创建
        </button>
        {once && (
          <p>
            完整密钥（仅显示一次）：<code>{once}</code>
          </p>
        )}
      </div>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>前缀</th>
              <th>别名</th>
              <th>模式</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(q.data?.items || []).map((k: any) => (
              <tr key={k.id}>
                <td>{k.key_prefix}</td>
                <td>{k.alias}</td>
                <td>{k.default_mode}</td>
                <td>{k.status}</td>
                <td>
                  <button
                    className="secondary"
                    onClick={() => api.revokeKey(k.id).then(() => qc.invalidateQueries({ queryKey: ["keys"] }))}
                  >
                    吊销
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
