import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle } from "@appica/ui-react/alert";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { CopyButton } from "@appica/ui-react/copy-button";
import { Input } from "@appica/ui-react/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@appica/ui-react/table";
import { api } from "../api";
import PageIntro from "../components/PageIntro";

export default function KeysPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("本机客户端");
  const [showCreate, setShowCreate] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [probeResult, setProbeResult] = useState<Record<string, string>>({});
  const keys = useQuery({ queryKey: ["gateway-keys"], queryFn: api.gatewayKeys });
  const items = keys.data?.keys || [];

  const create = useMutation({
    mutationFn: () => api.createGatewayKey(name),
    onSuccess: (data: any) => {
      setCreated(data.key);
      setShowCreate(false);
      queryClient.invalidateQueries({ queryKey: ["gateway-keys"] });
    },
  });
  const action = useMutation({
    mutationFn: async ({ type, id }: { type: "reveal" | "probe" | "delete" | "raise"; id?: string }) => {
      if (type === "reveal") return { type, id, data: await api.revealGatewayKey(id!) };
      if (type === "probe") return { type, id, data: await api.probeGatewayKey(id!) };
      if (type === "delete") return { type, id, data: await api.deleteGatewayKey(id!) };
      return { type, id, data: await api.raiseGatewayKeyLimits() };
    },
    onSuccess: (result: any) => {
      if (result.type === "reveal") setRevealed(current => ({ ...current, [result.id]: result.data.key }));
      if (result.type === "probe") setProbeResult(current => ({ ...current, [result.id]: result.data.message || (result.data.ok ? "连接正常" : "探测失败") }));
      queryClient.invalidateQueries({ queryKey: ["gateway-keys"] });
    },
  });

  return <div className="page-stack">
    <PageIntro title="API Key" actions={<Button onClick={() => setShowCreate(value => !value)}>{showCreate ? "取消" : "创建 Key"}</Button>} />
    <section className="key-purpose card"><div className="intelligence-mark key-mark" aria-hidden="true">K</div><div><strong>一个 Key 接入全部渠道</strong><p>默认使用 <code>auto-route</code>，完整 Key 保存在当前项目并随 jiyi 迁移。</p></div><Badge variant="success">可再次复制</Badge></section>

    {(showCreate || (!keys.isLoading && items.length === 0)) ? <section className="compact-key-create card"><div><label htmlFor="gateway-key-name">名称</label><Input id="gateway-key-name" value={name} onChange={event => setName(event.target.value)} placeholder="例如：本机 Codex" /></div><Button onClick={() => create.mutate()} disabled={create.isPending || !name.trim()}>{create.isPending ? "创建中…" : "生成"}</Button></section> : null}

    {created ? <Alert variant="success"><AlertTitle>Key 已创建并写入 jiyi 数据目录</AlertTitle><AlertDescription><span className="created-key"><code>{created}</code><CopyButton value={created} label="复制" copiedLabel="已复制" size="sm" /></span></AlertDescription></Alert> : null}
    {(create.isError || action.isError || keys.isError) ? <Alert variant="error"><AlertDescription>{((create.error || action.error || keys.error) as Error).message}</AlertDescription></Alert> : null}

    {items.length ? <div className="card table-surface key-table"><Table size="sm" borderStyle="none" hoverableRows><TableHeader><TableRow><TableHead>名称</TableHead><TableHead>Key</TableHead><TableHead>状态</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody>{items.map((item: any) => {
      const id = item.local_id || item.id || item.alias;
      const secret = revealed[id];
      return <TableRow key={id}><TableCell><strong>{item.alias}</strong><small className="key-meta">{item.rpm_limit ?? "—"} RPM · {item.tpm_limit ?? "—"} TPM</small></TableCell><TableCell>{secret ? <span className="inline-secret"><code>{secret}</code><CopyButton value={secret} label="复制" copiedLabel="已复制" size="sm" /></span> : <code>{item.key_preview || "sk-…"}</code>}</TableCell><TableCell>{probeResult[id] ? <Badge variant={probeResult[id].includes("正常") ? "success" : "warning"}>{probeResult[id]}</Badge> : <Badge variant={item.has_secret ? "success" : "secondary"}>{item.has_secret ? "可用" : "仅网关记录"}</Badge>}</TableCell><TableCell><div className="table-actions"><Button size="sm" disabled={!item.has_secret || action.isPending} onClick={() => action.mutate({ type: "reveal", id })}>显示并复制</Button><Button variant="ghost" size="sm" disabled={!item.has_secret || action.isPending} onClick={() => action.mutate({ type: "probe", id })}>探测</Button><Button variant="ghost" size="sm" disabled={action.isPending} onClick={() => window.confirm(`删除 ${item.alias}？`) && action.mutate({ type: "delete", id })}>删除</Button></div></TableCell></TableRow>;
    })}</TableBody></Table></div> : (!keys.isLoading ? <div className="quiet-empty">还没有客户端 Key。创建一个即可开始使用。</div> : null)}

    <details className="advanced-disclosure card"><summary>高级 Key 操作</summary><div className="advanced-key-actions"><p>旧版本创建的 Key 如果限额偏低，可以一次性恢复为当前聚合网关默认值。</p><Button variant="outline" size="sm" onClick={() => action.mutate({ type: "raise" })} disabled={action.isPending}>修正所有 Key 限额</Button></div></details>
  </div>;
}
