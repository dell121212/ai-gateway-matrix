import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle } from "@appica/ui-react/alert";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Separator } from "@appica/ui-react/separator";
import { api } from "../api";
import PageIntro from "../components/PageIntro";

export default function RuntimePage() {
  const health = useQuery({ queryKey: ["health", "runtime"], queryFn: api.health, refetchInterval: 3000, retry: false });
  const [sent, setSent] = useState("");
  const appParameter = new URLSearchParams(window.location.search).get("app") === "1";
  if (appParameter) window.sessionStorage.setItem("appicaDesktopShell", "1");
  const desktopShell = appParameter || window.sessionStorage.getItem("appicaDesktopShell") === "1";
  const healthy = Boolean(health.data?.postgres && health.data?.redis);
  const dispatch = (action: "start" | "restart" | "stop") => {
    if (!desktopShell) return;
    if ((action === "restart" || action === "stop") && !window.confirm(`${action === "stop" ? "停止" : "重启"}全部网关服务？`)) return;
    setSent(action);
    window.location.href = `ai-gateway://backend/${action}`;
  };
  return <div className="page-stack">
    <PageIntro eyebrow="Desktop runtime" title="后端控制" description="检查统一 4000 入口，并通过桌面壳管理 Docker 后端。" actions={<Button variant="outline" onClick={() => health.refetch()}>刷新状态</Button>} />
    <section className="card runtime-card">
      <div className="runtime-status"><span className={`status-orb${healthy ? " status-orb-ok" : ""}`} /><div><h3>{healthy ? "统一后端已连接" : "后端暂不可用"}</h3><p>{healthy ? "127.0.0.1:4000 · PostgreSQL · Redis" : (health.error as Error)?.message || "正在等待健康检查"}</p></div><Badge variant={healthy ? "success" : "warning"}>{healthy ? "运行中" : "离线"}</Badge></div>
      <Separator />
      <div className="runtime-actions"><Button disabled={!desktopShell || healthy} onClick={() => dispatch("start")}>启动</Button><Button variant="outline" disabled={!desktopShell} onClick={() => dispatch("restart")}>重启</Button><Button variant="outline" disabled={!desktopShell || !healthy} onClick={() => dispatch("stop")}>停止</Button></div>
    </section>
    {!desktopShell ? <Alert variant="info"><AlertTitle>浏览器模式不执行宿主命令</AlertTitle><AlertDescription>请通过 <code>bash run.sh</code> 打开桌面窗口，或在终端使用 <code>bash run.sh start|restart|stop</code>。</AlertDescription></Alert> : null}
    {sent ? <Alert variant="success"><AlertDescription>已向桌面壳发送 {sent} 命令，健康状态会自动刷新。</AlertDescription></Alert> : null}
  </div>;
}
