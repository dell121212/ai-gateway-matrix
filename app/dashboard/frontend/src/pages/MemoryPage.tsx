import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription } from "@appica/ui-react/alert";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@appica/ui-react/select";
import { Separator } from "@appica/ui-react/separator";
import { Switch } from "@appica/ui-react/switch";
import { useTheme } from "@appica/ui-react/hooks/use-theme";
import { api } from "../api";
import PageIntro from "../components/PageIntro";

export default function MemoryPage() {
  const queryClient = useQueryClient();
  const { setTheme } = useTheme();
  const status = useQuery({ queryKey: ["jiyi"], queryFn: api.jiyi, refetchInterval: 10_000 });
  const settings = useQuery({ queryKey: ["ui-settings"], queryFn: api.settings });
  const [draft, setDraft] = useState<Record<string, any> | null>(null);
  const sync = useMutation({
    mutationFn: api.syncJiyi,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jiyi"] }),
  });
  const saveSettings = useMutation({ mutationFn: () => api.updateSettings(draft || settings.data || {}), onSuccess: (result: any) => { if (result.theme) setTheme(result.theme); setDraft(null); queryClient.invalidateQueries({ queryKey: ["ui-settings"] }); } });
  const data = status.data || {};
  const rows = [
    ["自动同步", data.enabled, "文件变化与数据库快照会持续写入 jiyi"],
    ["客户端 Key", data.client_keys?.complete, data.client_keys?.complete ? `${data.client_keys.portable_count || 0} 个完整 Key 可随文件迁移` : "Key 携带状态需要检查"],
    ["数据库快照", data.database_snapshot?.exists, data.database_snapshot?.exists ? `${data.database_snapshot.size_bytes || 0} bytes · ${data.database_snapshot.updated_at || ""}` : "等待首次数据库快照"],
    ["Redis 快照", data.redis_snapshot?.exists, data.redis_snapshot?.exists ? `${data.redis_snapshot.size_bytes || 0} bytes · ${data.redis_snapshot.updated_at || ""}` : "等待首次 Redis 快照"],
    ["最近同步", Boolean(data.last_synced_at), data.last_synced_at || "尚未生成快照"],
  ] as const;
  return (
    <div className="page-stack">
      <PageIntro eyebrow="Portable memory" title="设置" description="检查 jiyi 中的设置、Key、Token、路由与数据库快照。" actions={<Button onClick={() => sync.mutate()} disabled={sync.isPending}>{sync.isPending ? "同步中…" : "立即同步"}</Button>} />
      {sync.isError ? <Alert variant="error"><AlertDescription>{(sync.error as Error).message}</AlertDescription></Alert> : null}
      {sync.isSuccess ? <Alert variant="success"><AlertDescription>{(sync.data as any)?.message || "同步请求已接受"}</AlertDescription></Alert> : null}
      <section className="memory-grid">
        {rows.map(([label, okay, detail]) => (
          <article className="card memory-card" key={label}>
            <div><Badge variant={okay ? "success" : "warning"}>{okay ? "就绪" : "待检查"}</Badge><h3>{label}</h3></div>
            <Separator />
            <p>{String(detail)}</p>
          </article>
        ))}
      </section>
      <section className="card settings-panel">
        <div className="form-section-head"><div><p className="section-kicker">Application settings</p><h3>应用设置</h3><p>主题会同步到 Appica，自启动设置保留在项目数据中。</p></div></div>
        <div className="settings-grid">
          <label><span>主题</span><Select value={(draft || settings.data)?.theme || "system"} onValueChange={(value) => setDraft({ ...(settings.data || {}), ...(draft || {}), theme: String(value) })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="system">跟随系统</SelectItem><SelectItem value="light">亮色</SelectItem><SelectItem value="dark">深色</SelectItem></SelectContent></Select></label>
          <label><span>语言</span><Select value={(draft || settings.data)?.language || "zh"} onValueChange={(value) => setDraft({ ...(settings.data || {}), ...(draft || {}), language: String(value) })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="zh">简体中文</SelectItem><SelectItem value="en">English</SelectItem></SelectContent></Select></label>
          <div className="setting-switch"><div><strong>开机自启动</strong><span>登录桌面后自动启动应用</span></div><Switch checked={Boolean((draft || settings.data)?.autostart)} onCheckedChange={(checked) => setDraft({ ...(settings.data || {}), ...(draft || {}), autostart: checked })} /></div>
          <div className="setting-switch"><div><strong>静默启动</strong><span>自启动时不主动抢占前台</span></div><Switch checked={Boolean((draft || settings.data)?.autostart_silent)} onCheckedChange={(checked) => setDraft({ ...(settings.data || {}), ...(draft || {}), autostart_silent: checked })} /></div>
        </div>
        <div className="form-actions"><Button variant="outline" onClick={() => setDraft(null)}>取消更改</Button><Button disabled={!draft || saveSettings.isPending} onClick={() => saveSettings.mutate()}>{saveSettings.isPending ? "保存中…" : "保存设置"}</Button></div>
      </section>
      {saveSettings.isError ? <Alert variant="error"><AlertDescription>{(saveSettings.error as Error).message}</AlertDescription></Alert> : null}
      <section className="card memory-path"><span>记忆文件</span><code>{data.path || data.jiyi_path || "jiyi.txt"}</code><Button variant="outline" onClick={() => status.refetch()}>重新检查</Button></section>
    </div>
  );
}
