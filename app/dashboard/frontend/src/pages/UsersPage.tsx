import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription } from "@appica/ui-react/alert";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Input } from "@appica/ui-react/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@appica/ui-react/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@appica/ui-react/table";
import { api } from "../api";
import PageIntro from "../components/PageIntro";

export default function UsersPage() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "user" });
  const create = useMutation({ mutationFn: () => api.createUser(form), onSuccess: () => { setForm({ ...form, username: "", display_name: "", password: "" }); qc.invalidateQueries({ queryKey: ["users"] }); } });
  const disable = useMutation({ mutationFn: api.disableUser, onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }) });
  return (
    <div className="page-stack">
      <PageIntro eyebrow="Identity & roles" title="用户管理" description="创建账户、分配角色，并停用不再需要的账户。" />
      <section className="card entity-form">
        <div className="form-section-head"><div><p className="section-kicker">New user</p><h3>创建用户</h3></div><Badge variant="primary-outline">管理员操作</Badge></div>
        <div className="entity-form-grid">
          <Input aria-label="用户名" placeholder="用户名" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <Input aria-label="显示名" placeholder="显示名" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <Input aria-label="密码" type="password" placeholder="密码（至少 10 位）" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <Select value={form.role} onValueChange={(value) => setForm({ ...form, role: String(value) })}><SelectTrigger aria-label="角色"><SelectValue /></SelectTrigger><SelectContent>{["user", "auditor", "operator", "billing_admin", "super_admin"].map((role) => <SelectItem value={role} key={role}>{role}</SelectItem>)}</SelectContent></Select>
          <Button onClick={() => create.mutate()} disabled={create.isPending || !form.username || form.password.length < 10}>{create.isPending ? "创建中…" : "创建用户"}</Button>
        </div>
      </section>
      {(create.isError || disable.isError) ? <Alert variant="error"><AlertDescription>{((create.error || disable.error) as Error).message}</AlertDescription></Alert> : null}
      <div className="card table-surface"><Table size="sm" borderStyle="none" hoverableRows><TableHeader><TableRow><TableHead>用户</TableHead><TableHead>显示名</TableHead><TableHead>角色</TableHead><TableHead>状态</TableHead><TableHead>最近登录</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody>{(users.data?.items || []).map((user: any) => <TableRow key={user.id}><TableCell>{user.username}</TableCell><TableCell>{user.display_name}</TableCell><TableCell><Badge variant="info">{user.role}</Badge></TableCell><TableCell><Badge variant={user.status === "active" ? "success" : "secondary"}>{user.status}</Badge></TableCell><TableCell className="muted">{user.last_login_at || "—"}</TableCell><TableCell><Button variant="outline" size="sm" disabled={user.status !== "active" || disable.isPending} onClick={() => window.confirm(`停用 ${user.username}？`) && disable.mutate(user.id)}>停用</Button></TableCell></TableRow>)}</TableBody></Table></div>
    </div>
  );
}
