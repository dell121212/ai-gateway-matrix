import { useState } from "react";
import { Alert, AlertDescription } from "@appica/ui-react/alert";
import { BackgroundPattern } from "@appica/ui-react/background-pattern";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Input } from "@appica/ui-react/input";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

export default function LoginPage({ hasUsers }: { hasUsers: boolean }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      if (!hasUsers) await api.bootstrap(username, password);
      await api.login(username, password);
      navigate("/");
    } catch (caught: any) {
      setError(caught?.message || "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <BackgroundPattern variant="dots" spotlight={{ size: 500, persistent: true }} track="window" className="login-shell">
      <section className="card login-card">
        <div className="login-brand"><span className="brand-mark">P</span><Badge variant="primary-outline">安全访问</Badge></div>
        <h2>{hasUsers ? "欢迎回来" : "初始化管理员"}</h2>
        <form onSubmit={submit}>
          <label htmlFor="login-username">用户名</label>
          <Input id="login-username" className="field-control" value={username} onChange={(event) => setUsername(event.target.value)} required />
          <label htmlFor="login-password">密码</label>
          <Input id="login-password" className="field-control" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={10} />
          {error ? <Alert variant="error"><AlertDescription>{error}</AlertDescription></Alert> : null}
          <Button className="login-submit" type="submit" disabled={loading}>{loading ? "处理中…" : hasUsers ? "进入工作台" : "创建并进入"}</Button>
        </form>
      </section>
    </BackgroundPattern>
  );
}
