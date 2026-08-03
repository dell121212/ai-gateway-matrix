import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle } from "@appica/ui-react/alert";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@appica/ui-react/dialog";
import { Input } from "@appica/ui-react/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@appica/ui-react/select";
import { Textarea } from "@appica/ui-react/textarea";
import { api, formatTokens } from "../api";
import { cleanPeriodLabel, periodTime, resolveQuota } from "../channelQuota";
import PageIntro from "../components/PageIntro";

type Channel = Record<string, any> & { channel_id: string };
type Notice = { kind: "success" | "error"; title: string; message: string } | null;

const tierOptions = ["全部", "顶级", "强", "中", "弱"];
const pools = [
  ["fast-pool", "弱"],
  ["free-pool", "中"],
  ["strong-model-pool", "强"],
  ["elite-model-pool", "顶级"],
];

function numberValue(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : "待返回";
}

function QuotaBoard({ channel, sharedModelCount, onRefresh, refreshing }: { channel: Channel; sharedModelCount: number; onRefresh: () => void; refreshing: boolean }) {
  const quota = resolveQuota(channel);
  const billing = channel.billing_info || {};
  const official = billing.official || {};
  const showBilling = billing.supports_official_balance || official.summary_zh || official.message || billing.local_spend_summary_zh || billing.message;
  const remaining = quota.remaining == null || !Number.isFinite(quota.remaining) ? null : Math.max(0, Math.min(100, quota.remaining));
  const remainingText = remaining == null ? "未提供" : `${Number(remaining.toFixed(1))}%`;
  const warning = remaining != null && remaining < 15;
  return <section className="quota-board" aria-label={`${channel.company_name || channel.provider_name}额度看板`}>
    <div className="quota-compact-stats">
      <div><span>总消耗 Token</span><strong>{quota.totalTokens == null ? "—" : formatTokens(Number(quota.totalTokens))}</strong></div>
      <div className={warning ? "quota-compact-warning" : ""}><span>当前周期余量</span><strong>{remainingText}</strong></div>
      <div><span>更新周期</span><strong>{quota.label}</strong><b>{periodTime(quota.seconds, quota.windowSeconds)}</b></div>
    </div>
    <div className={`quota-progress${warning ? " quota-progress-warning" : ""}${remaining == null ? " quota-progress-unknown" : ""}`} role="progressbar" aria-label="当前周期余量" aria-valuemin={0} aria-valuemax={100} aria-valuenow={remaining ?? undefined}><i style={{ width: `${remaining ?? 0}%` }} /></div>
    <details className="quota-extra">
      <summary>详细信息</summary>
      <div className="quota-board-badges">
        {channel.free_quota_label_zh ? <Badge variant="soft" size="xs">{channel.free_quota_label_zh}</Badge> : null}
        {channel.pricing_label_zh ? <Badge variant="soft" size="xs">{channel.pricing_label_zh}</Badge> : null}
        {channel.quota_shared ? <Badge variant="primary-outline" size="xs">{sharedModelCount > 1 ? `Key 共用 · ${sharedModelCount} 模型` : "Key 额度"}</Badge> : null}
      </div>
      {(channel.how_free_zh || channel.rate_limits?.note_zh) ? <p className="quota-notice">{[channel.how_free_zh, channel.rate_limits?.note_zh].filter(Boolean).join(" · ")}</p> : null}
      {quota.windows.length ? <details className="quota-windows"><summary>额度窗口 {quota.windows.length} 项</summary><div>{quota.windows.map((item: any) => <div className="quota-window-row" key={item.id}><span><strong>{cleanPeriodLabel(item.label_zh || item.window_label)}</strong><small>{periodTime(item.seconds_until_reset, item.window_sec)}</small></span><b>{item.limit == null ? "上游未提供限额" : `${item.used == null ? "待返回" : numberValue(item.used)} / ${numberValue(item.limit)}`}</b></div>)}</div></details> : null}
      {channel.rate_limits?.docs_url ? <a className="quota-doc-link" href={channel.rate_limits.docs_url} target="_blank" rel="noreferrer">供应商规则 ↗</a> : null}
      {showBilling ? <div className="quota-billing"><div className="quota-billing-head"><strong>余额 / 消费</strong>{billing.supports_official_balance && channel.is_configured ? <Button variant="ghost" size="sm" disabled={refreshing} onClick={onRefresh}>{refreshing ? "刷新中…" : "刷新余额"}</Button> : null}</div><div><span>官方</span><p>{official.summary_zh || official.message || (billing.supports_official_balance ? "官方余额尚未刷新" : "该渠道不支持官方余额查询")}</p></div><div><span>本机</span><p>{billing.local_spend_summary_zh || billing.message || "本机尚无消费记录"}</p></div></div> : null}
    </details>
  </section>;
}

function ChannelCard({ channel, sharedModelCount, busy, perform }: { channel: Channel; sharedModelCount: number; busy: string; perform: (label: string, action: () => Promise<any>) => Promise<void> }) {
  const [key, setKey] = useState("");
  const [model, setModel] = useState(channel.model_display || channel.model || "");
  const [priority, setPriority] = useState(String(channel.priority ?? 0));
  const [optimalEditor, setOptimalEditor] = useState(false);
  const [optimalReason, setOptimalReason] = useState("");
  const [optimalHours, setOptimalHours] = useState("");
  const running = busy === channel.channel_id;
  const update = (label: string, action: () => Promise<any>) => perform(label, action);
  return (
    <article className={`channel-card card${channel.is_optimal ? " channel-optimal" : ""}`}>
      <header className="channel-card-head">
        <div className="channel-identity">
          <div><h3>{channel.company_name || channel.provider_name}</h3><p>{channel.account_label || channel.env_var}</p></div>
        </div>
        <div className="channel-badges">
          <Badge variant={channel.is_configured ? "success" : "warning"}>{channel.is_configured ? "已配置" : "未填 Key"}</Badge>
          <Badge variant="primary-outline">{channel.tier || "未分档"}</Badge>
          {channel.is_optimal ? <Badge variant="warning">限时优先</Badge> : null}
        </div>
      </header>

      <div className="channel-model-line"><span>当前模型</span><code>{channel.model_display || channel.model}</code><span className={`connection-dot${channel.connection_ok ? " ok" : ""}`} />{channel.connection_message || "未检查"}</div>

      <QuotaBoard channel={channel} sharedModelCount={sharedModelCount} refreshing={running} onRefresh={() => update("刷新余额", () => api.channelBalance(channel.channel_id))} />

      <details className="channel-details">
        <summary>用量、费用、顺畅度与能力</summary>
        <div className="channel-detail-grid">
          <div><span>今日 / 累计 Token</span><strong>{channel.usage?.day_tokens ?? "—"} / {channel.usage?.total_tokens ?? "—"}</strong></div>
          <div><span>本机等值花费</span><strong>{channel.billing_info?.local_spend_summary_zh || "—"}</strong></div>
          <div><span>顺畅度</span><strong>{channel.smoothness?.label || "—"} · {channel.smoothness?.hint_zh || "暂无样本"}</strong></div>
          <div><span>数据策略</span><strong>{channel.data_policy || "—"} · {channel.sensitive_allowed ? "可用敏感数据" : "不建议敏感数据"}</strong></div>
          <div className="span-2"><span>能力</span><div className="capability-list">{Object.entries(channel.capabilities || {}).filter(([, enabled]) => enabled).map(([name]) => <Badge variant="soft" size="xs" key={name}>{name}</Badge>)}</div></div>
        </div>
        {channel.rate_limits?.docs_url ? <Button variant="outline" size="sm" render={<a href={channel.rate_limits.docs_url} target="_blank" rel="noreferrer" />}>查看官方额度文档 ↗</Button> : null}
      </details>

      <details className="channel-details channel-editor">
        <summary>编辑渠道</summary>
        <div className="channel-edit-grid">
          <label><span>模型 ID</span><div className="inline-control"><Input value={model} onChange={(event) => setModel(event.target.value)} /><Button variant="outline" size="sm" disabled={running || !model.trim()} onClick={() => update("保存模型", () => api.updateChannelModel(channel.channel_id, model))}>保存</Button></div></label>
          <label><span>优先级</span><div className="inline-control"><Input type="number" min="0" max="1000" value={priority} onChange={(event) => setPriority(event.target.value)} /><Button variant="outline" size="sm" disabled={running} onClick={() => update("保存优先级", () => api.updateChannelPriority(channel.channel_id, Number(priority)))}>保存</Button></div></label>
          <label><span>模型档位</span><Select value={channel.tier_pool} onValueChange={(value) => update("保存档位", () => api.updateChannelTier(channel.channel_id, String(value)))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{pools.map(([value, label]) => <SelectItem value={value} key={value}>{label}</SelectItem>)}</SelectContent></Select></label>
          <label><span>上游 API Key</span><div className="inline-control"><Input type="password" placeholder={channel.masked_key || "粘贴 API Key"} value={key} onChange={(event) => setKey(event.target.value)} /><Button size="sm" disabled={running || !key.trim()} onClick={() => update("保存渠道 Key", async () => { const result = await api.updateChannelKey(channel.channel_id, key); setKey(""); return result; })}>保存并检查</Button></div></label>
        </div>
      </details>

      <div className="channel-actions">
        <Button variant="outline" size="sm" disabled={running || !channel.is_configured} onClick={() => update("检查连接", () => api.channelProbe(channel.channel_id))}>检查连接</Button>
        <Button variant="outline" size="sm" disabled={running || !channel.is_configured} onClick={() => update("设为智脑", () => api.updateRoutingControl({ mode: "source_env", source_env: channel.env_var, model: channel.model, api_base: channel.api_base || "", exclusive: true, answer_verify_mode: "hybrid" }))}>设为智脑</Button>
        <Button variant="outline" size="sm" disabled={running} onClick={() => channel.is_optimal ? update("取消限时优先", () => api.clearChannelOptimal(channel.channel_id)) : setOptimalEditor(!optimalEditor)}>{channel.is_optimal ? "取消限时优先" : "限时优先"}</Button>
      </div>
      <div className="channel-actions channel-account-actions" aria-label="额度账号操作">
        <Button variant="outline" size="sm" disabled={running || !channel.company_id} onClick={() => update("新增同厂账号", () => api.addCompanyAccount(channel.company_id))}>新增账号</Button>
        <Button className="danger-action" variant="outline" size="sm" disabled={running} onClick={() => window.confirm(`删除 ${channel.provider_name} / ${channel.model_display || channel.model}？`) && update("删除渠道", () => api.deleteChannel(channel.channel_id))}>删除渠道</Button>
      </div>
      {optimalEditor ? <div className="optimal-editor"><Input placeholder="原因，例如试用额度快过期" value={optimalReason} onChange={(event) => setOptimalReason(event.target.value)} /><Input type="number" min="0" step="0.5" placeholder="过期小时，留空=手动取消" value={optimalHours} onChange={(event) => setOptimalHours(event.target.value)} /><Button size="sm" onClick={() => update("设为限时优先", async () => { const result = await api.setChannelOptimal(channel.channel_id, { reason: optimalReason, ...(optimalHours ? { expires_in_hours: Number(optimalHours) } : {}) }); setOptimalEditor(false); return result; })}>确认标记</Button><Button variant="outline" size="sm" onClick={() => setOptimalEditor(false)}>取消</Button></div> : null}
      {running ? <p className="channel-busy">正在与后端同步…</p> : null}
    </article>
  );
}

function RoutingPanel({ channels, perform }: { channels: Channel[]; perform: (label: string, action: () => Promise<any>) => Promise<void> }) {
  const routing = useQuery({ queryKey: ["routing-control"], queryFn: api.routingControl });
  const [draft, setDraft] = useState<Record<string, any> | null>(null);
  const [checking, setChecking] = useState(false);
  const [probe, setProbe] = useState<{ ok: boolean; message: string } | null>(null);
  const value = draft || routing.data || { mode: "auto", answer_verify_mode: "hybrid", exclusive: true };
  const patch = (key: string, next: any) => { setProbe(null); setDraft({ ...value, [key]: next }); };
  const sources = Array.from(new Set(channels.filter((item) => item.is_configured).map((item) => item.env_var).filter(Boolean)));
  const checkConnection = async () => {
    setChecking(true);
    setProbe(null);
    try {
      const result = await api.probeRoutingControl();
      setProbe({ ok: Boolean(result.ok), message: result.message || (result.ok ? "智脑与答检连接正常" : "连接失败") });
    } catch (error) {
      setProbe({ ok: false, message: (error as Error).message });
    } finally {
      setChecking(false);
    }
  };
  return <section className="card routing-panel">
    <div className="form-section-head"><div><p className="section-kicker">Classifier brain</p><h3>智脑与答检</h3><p>指定分诊模型来源与回答质检策略。</p></div><Badge variant={routing.data?.has_dedicated ? "success" : "secondary"}>{routing.data?.active_label || "自动"}</Badge></div>
    <div className="routing-form-grid">
      <label><span>智脑模式</span><Select value={value.mode || "auto"} onValueChange={(next) => patch("mode", String(next))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="auto">自动选择</SelectItem><SelectItem value="source_env">使用已配置渠道</SelectItem><SelectItem value="dedicated_key">独立专用 Key</SelectItem></SelectContent></Select></label>
      {value.mode === "source_env" ? <label><span>渠道账号</span><Select value={value.source_env || ""} onValueChange={(next) => patch("source_env", String(next))}><SelectTrigger><SelectValue placeholder="选择已配置账号" /></SelectTrigger><SelectContent>{sources.map((source) => <SelectItem value={source} key={source}>{source}</SelectItem>)}</SelectContent></Select></label> : null}
      <label><span>答检模式</span><Select value={value.answer_verify_mode || "hybrid"} onValueChange={(next) => patch("answer_verify_mode", String(next))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="hybrid">混合</SelectItem><SelectItem value="local">本地</SelectItem><SelectItem value="dedicated">专用智脑</SelectItem><SelectItem value="off">关闭</SelectItem></SelectContent></Select></label>
      <label><span>模型</span><Input value={value.model || ""} onChange={(event) => patch("model", event.target.value)} placeholder="例如 glm-5" /></label>
      <label><span>API Base</span><Input value={value.api_base || ""} onChange={(event) => patch("api_base", event.target.value)} placeholder="https://api.example.com/v1" /></label>
      {value.mode === "dedicated_key" ? <label><span>专用 API Key</span><Input type="password" value={value.api_key || ""} onChange={(event) => patch("api_key", event.target.value)} placeholder={routing.data?.has_dedicated ? "留空则保留现有 Key" : "粘贴专用 Key"} /></label> : null}
    </div>
    <div className="form-actions"><Button variant="outline" onClick={() => { setProbe(null); setDraft(null); routing.refetch(); }}>重置</Button><Button variant="outline" disabled={checking || Boolean(draft)} title={draft ? "请先保存当前修改" : undefined} onClick={checkConnection}>{checking ? "检查中…" : "检查智脑连接"}</Button><Button onClick={() => perform("保存智脑设置", async () => { const result = await api.updateRoutingControl(value); setProbe(null); setDraft(null); await routing.refetch(); return result; })}>保存智脑设置</Button></div>
    {probe ? <Alert className="routing-probe-result" variant={probe.ok ? "success" : "error"}><AlertDescription>{probe.message}</AlertDescription></Alert> : null}
  </section>;
}

function CustomProviderDialog({ open, onOpenChange, perform }: { open: boolean; onOpenChange: (open: boolean) => void; perform: (label: string, action: () => Promise<any>) => Promise<void> }) {
  const [form, setForm] = useState({ provider_name: "", api_base: "", api_key: "", model: "", pool: "free-pool", snippet: "" });
  const [models, setModels] = useState<any[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const discover = async () => {
    setDiscovering(true);
    try { const result = form.snippet.trim() ? await api.parseCustomProvider({ snippet: form.snippet, discover: true }) : await api.discoverCustomProvider(form); const parsed = result.parsed || {}; const list = result.models || []; setModels(list); setForm((current) => ({ ...current, provider_name: parsed.provider_name || current.provider_name, api_base: parsed.api_base || result.api_base || current.api_base, api_key: parsed.api_key || current.api_key, model: result.recommended?.id || parsed.model || list[0]?.id || current.model, pool: result.recommended?.pool || list[0]?.pool || current.pool })); } finally { setDiscovering(false); }
  };
  const selected = models.find((item) => item.id === form.model);
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="custom-provider-dialog"><DialogHeader><DialogTitle>添加自定义 OpenAI 兼容 API</DialogTitle><DialogDescription>可直接填写，也可粘贴官方文档、curl 或 .env 片段让智脑解析。</DialogDescription></DialogHeader><DialogBody>
    <div className="dialog-form-grid"><label className="span-2"><span>粘贴文档 / curl（可选）</span><Textarea rows={5} value={form.snippet} onChange={(event) => setForm({ ...form, snippet: event.target.value })} /></label><label><span>提供方</span><Input value={form.provider_name} onChange={(event) => setForm({ ...form, provider_name: event.target.value })} /></label><label><span>API Base</span><Input value={form.api_base} onChange={(event) => setForm({ ...form, api_base: event.target.value })} /></label><label className="span-2"><span>API Key</span><Input type="password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} /></label><label><span>模型</span>{models.length ? <Select value={form.model} onValueChange={(value) => { const item = models.find((candidate) => candidate.id === value); setForm({ ...form, model: String(value), pool: item?.pool || form.pool }); }}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{models.map((item) => <SelectItem value={item.id} key={item.id}>{item.id} · {item.tier}</SelectItem>)}</SelectContent></Select> : <Input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} />}</label><label><span>模型档位</span><Select value={form.pool} onValueChange={(value) => setForm({ ...form, pool: String(value) })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{pools.map(([value, label]) => <SelectItem value={value} key={value}>{label}</SelectItem>)}</SelectContent></Select></label></div>
    {selected ? <Alert variant="info"><AlertDescription>{selected.reason}</AlertDescription></Alert> : null}
  </DialogBody><DialogFooter><Button variant="outline" onClick={discover} disabled={discovering || (!form.snippet.trim() && (!form.api_base || !form.api_key))}>{discovering ? "发现中…" : form.snippet.trim() ? "解析并发现模型" : "发现模型"}</Button><Button disabled={!form.provider_name || !form.api_base || !form.api_key || !form.model} onClick={() => perform("添加自定义 API", async () => { const result = await api.addCustomProvider(form); onOpenChange(false); return result; })}>添加渠道</Button></DialogFooter></DialogContent></Dialog>;
}

export default function ChannelsPage() {
  const qc = useQueryClient();
  const channels = useQuery({ queryKey: ["channels"], queryFn: api.channels });
  const [query, setQuery] = useState("");
  const [tier, setTier] = useState("全部");
  const [configuredOnly, setConfiguredOnly] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const items = channels.data?.channels || [];
  const visible = useMemo(() => items.filter((channel: Channel) => { const text = `${channel.company_name} ${channel.provider_name} ${channel.model} ${channel.env_var}`.toLowerCase(); return (!query || text.includes(query.toLowerCase())) && (tier === "全部" || channel.tier === tier) && (!configuredOnly || channel.is_configured); }).sort((a: Channel, b: Channel) => Number(b.priority || 0) - Number(a.priority || 0)), [items, query, tier, configuredOnly]);
  const perform = async (label: string, action: () => Promise<any>) => { setNotice(null); try { const result = await action(); setNotice({ kind: "success", title: label, message: result?.message || "操作已完成" }); await Promise.all([qc.invalidateQueries({ queryKey: ["channels"] }), qc.invalidateQueries({ queryKey: ["routing-control"] })]); } catch (error) { setNotice({ kind: "error", title: `${label}失败`, message: (error as Error).message }); } finally { setBusy(""); } };
  const channelPerform = async (channel: Channel, label: string, action: () => Promise<any>) => { setBusy(channel.channel_id); await perform(label, action); };
  const companies = new Set(items.map((item: Channel) => item.company_id || item.company_name)).size;
  return <div className="page-stack channels-page">
    <PageIntro eyebrow="Brain routing" title="渠道与额度" description="每个模型独立分档、计量和熔断；智脑只把请求送进合适档位。" actions={<><Button variant="outline" onClick={() => channels.refetch()} disabled={channels.isFetching}>{channels.isFetching ? "刷新中…" : "刷新渠道"}</Button><Button onClick={() => setCustomOpen(true)}>+自定义 API</Button></>} />
    <section className="channel-stats">{[["模型渠道", items.length], ["厂商", companies], ["已配置", items.filter((item: Channel) => item.is_configured).length], ["已跟踪窗口", items.reduce((sum: number, item: Channel) => sum + (item.rate_limits?.windows || []).filter((window: any) => window.used != null).length, 0)]].map(([label, value]) => <article className="card" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
    {notice ? <Alert variant={notice.kind}><AlertTitle>{notice.title}</AlertTitle><AlertDescription>{notice.message}</AlertDescription></Alert> : null}
    <RoutingPanel channels={items} perform={perform} />
    <section className="channel-toolbar card"><Input aria-label="搜索渠道" placeholder="搜索公司、模型或环境变量…" value={query} onChange={(event) => setQuery(event.target.value)} /><div className="tier-filter">{tierOptions.map((option) => <Button key={option} variant={tier === option ? "primary" : "outline"} size="sm" onClick={() => setTier(option)}>{option}</Button>)}</div><Button variant={configuredOnly ? "primary" : "outline"} size="sm" onClick={() => setConfiguredOnly(!configuredOnly)}>仅已配置</Button><Badge variant="soft">{visible.length} 个结果</Badge></section>
    {channels.isError ? <Alert variant="error"><AlertTitle>渠道加载失败</AlertTitle><AlertDescription>{(channels.error as Error).message}</AlertDescription></Alert> : null}
    <section className="channel-grid">{visible.map((channel: Channel) => <ChannelCard key={channel.channel_id} channel={channel} sharedModelCount={items.filter((item: Channel) => item.env_var && item.env_var === channel.env_var).length || 1} busy={busy} perform={(label, action) => channelPerform(channel, label, action)} />)}</section>
    {!channels.isLoading && !visible.length ? <div className="card table-empty">没有符合筛选条件的渠道。</div> : null}
    <CustomProviderDialog open={customOpen} onOpenChange={setCustomOpen} perform={perform} />
  </div>;
}
