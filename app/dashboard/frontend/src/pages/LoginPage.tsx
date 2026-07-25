import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

export default function LoginPage({ hasUsers }: { hasUsers: boolean }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const nav = useNavigate();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      if (!hasUsers) {
        await api.bootstrap(username, password);
      }
      await api.login(username, password);
      nav("/");
    } catch (err: any) {
      setError(err?.message || "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="main" style={{ maxWidth: 420, margin: "4rem auto" }}>
      <div className="card">
        <h2>{hasUsers ? "登录" : "初始化管理员"}</h2>
        <p className="muted">账户模式（DASHBOARD_AUTH=accounts）。密码不会保存在 localStorage。</p>
        <form onSubmit={submit}>
          <label>用户名</label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} required />
          <label>密码</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={10}
          />
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={loading}>
            {loading ? "处理中…" : hasUsers ? "登录" : "创建并登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
