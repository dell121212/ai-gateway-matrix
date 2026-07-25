import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import LoginPage from "./pages/LoginPage";
import OverviewPage from "./pages/OverviewPage";
import TasksPage from "./pages/TasksPage";
import TaskDetailPage from "./pages/TaskDetailPage";
import LedgerPage from "./pages/LedgerPage";
import KeysPage from "./pages/KeysPage";
import RequestsPage from "./pages/RequestsPage";
import PricingPage from "./pages/PricingPage";
import AuditPage from "./pages/AuditPage";
import HealthPage from "./pages/HealthPage";
import ChannelsPage from "./pages/ChannelsPage";

export default function App() {
  const status = useQuery({ queryKey: ["auth-status"], queryFn: api.authStatus, retry: false });

  if (status.isLoading) {
    return <div className="main muted">加载中…</div>;
  }

  const authed =
    status.data?.authenticated ||
    status.data?.auth_mode === "local" ||
    status.data?.auth_mode === "token";

  if (!authed && status.data?.auth_mode === "accounts") {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage hasUsers={!!status.data?.has_users} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <div className="layout">
      <nav className="nav">
        <h1>Private-API 控制台</h1>
        <NavLink to="/" end>
          总览
        </NavLink>
        <NavLink to="/tasks">实时任务</NavLink>
        <NavLink to="/requests">请求明细</NavLink>
        <NavLink to="/ledger">积分账本</NavLink>
        <NavLink to="/keys">API Key</NavLink>
        <NavLink to="/pricing">模型定价</NavLink>
        <NavLink to="/channels">渠道路由</NavLink>
        <NavLink to="/audit">审计日志</NavLink>
        <NavLink to="/health">系统健康</NavLink>
        <p className="muted" style={{ padding: "0.75rem", fontSize: "0.75rem" }}>
          经典渠道台：
          <a href="/" target="_blank" rel="noreferrer">
            /
          </a>
        </p>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/tasks/:id" element={<TaskDetailPage />} />
          <Route path="/requests" element={<RequestsPage />} />
          <Route path="/ledger" element={<LedgerPage />} />
          <Route path="/keys" element={<KeysPage />} />
          <Route path="/pricing" element={<PricingPage />} />
          <Route path="/channels" element={<ChannelsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/login" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
