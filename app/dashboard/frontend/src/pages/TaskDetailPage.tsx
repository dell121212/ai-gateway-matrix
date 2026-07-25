import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, formatCredits } from "../api";

type LiveEvt = { event?: string; request_estimated_microcredits?: number; request_settled_microcredits?: number; settled?: boolean };

export default function TaskDetailPage() {
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["task", id], queryFn: () => api.task(id), refetchInterval: 5000 });
  const [live, setLive] = useState<LiveEvt[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!id) return;
    const es = new EventSource(`/api/v1/live/tasks/${id}/events`, { withCredentials: true } as any);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    const onMsg = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data);
        setLive((prev) => [data, ...prev].slice(0, 50));
      } catch {
        /* ignore */
      }
    };
    es.addEventListener("credits.estimated", onMsg);
    es.addEventListener("credits.settled", onMsg);
    es.addEventListener("credits.reserved", onMsg);
    es.onmessage = onMsg;
    return () => es.close();
  }, [id]);

  const t = q.data?.task;
  return (
    <div>
      <h2>任务详情</h2>
      {!t ? (
        <p className="muted">加载中…</p>
      ) : (
        <>
          <div className="card">
            <div className="row">
              <span className="badge">{t.status}</span>
              <span className="muted">SSE: {connected ? "已连接" : "重连中…"}</span>
            </div>
            <p>
              估算{" "}
              <span className="badge est">{formatCredits(t.estimated_microcredits, true)}</span> ·
              已结算 <span className="badge settled">{formatCredits(t.settled_microcredits)}</span>
            </p>
            <p className="muted">ID: {t.id}</p>
          </div>
          <div className="card">
            <h3>实时事件（后台驱动，非随机动画）</h3>
            <ul>
              {live.map((e, i) => (
                <li key={i}>
                  {e.event}{" "}
                  {e.request_estimated_microcredits != null && (
                    <span className="badge est">
                      ~{formatCredits(e.request_estimated_microcredits)}
                    </span>
                  )}
                  {e.request_settled_microcredits != null && (
                    <span className="badge settled">
                      {formatCredits(e.request_settled_microcredits)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
            {!live.length && <p className="muted">等待事件…</p>}
          </div>
          <div className="card">
            <h3>请求</h3>
            <table>
              <thead>
                <tr>
                  <th>请求</th>
                  <th>模式</th>
                  <th>模型</th>
                  <th>状态</th>
                  <th>估算</th>
                  <th>结算</th>
                </tr>
              </thead>
              <tbody>
                {(q.data?.requests || []).map((r: any) => (
                  <tr key={r.id}>
                    <td>{r.id.slice(0, 8)}…</td>
                    <td>{r.mode}</td>
                    <td>{r.requested_model}</td>
                    <td>{r.status}</td>
                    <td>{formatCredits(r.estimated_microcredits, true)}</td>
                    <td>{formatCredits(r.settled_microcredits)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
