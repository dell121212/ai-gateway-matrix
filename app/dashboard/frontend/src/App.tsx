import { lazy, Suspense, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, AlertDescription } from "@appica/ui-react/alert";
import { BackgroundPattern } from "@appica/ui-react/background-pattern";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Input } from "@appica/ui-react/input";
import {
  Navigation,
  NavigationItem,
  NavigationLink,
  NavigationList,
} from "@appica/ui-react/navigation";
import { Skeleton } from "@appica/ui-react/skeleton";
import { useTheme } from "@appica/ui-react/hooks/use-theme";
import {
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { api } from "./api";
import LoginPage from "./pages/LoginPage";

const AuditPage = lazy(() => import("./pages/AuditPage"));
const ChannelsPage = lazy(() => import("./pages/ChannelsPage"));
const ConnectionPage = lazy(() => import("./pages/ConnectionPage"));
const HealthPage = lazy(() => import("./pages/HealthPage"));
const KeysPage = lazy(() => import("./pages/KeysPage"));
const UsagePage = lazy(() => import("./pages/UsagePage"));
const OverviewPage = lazy(() => import("./pages/OverviewPage"));
const PricingPage = lazy(() => import("./pages/PricingPage"));
const RequestDetailPage = lazy(() => import("./pages/RequestDetailPage"));
const RequestsPage = lazy(() => import("./pages/RequestsPage"));
const TaskDetailPage = lazy(() => import("./pages/TaskDetailPage"));
const TasksPage = lazy(() => import("./pages/TasksPage"));
const MemoryPage = lazy(() => import("./pages/MemoryPage"));
const UsersPage = lazy(() => import("./pages/UsersPage"));
const RuntimePage = lazy(() => import("./pages/RuntimePage"));

type NavItem = {
  to: string;
  label: string;
  icon: "overview" | "connection" | "tasks" | "requests" | "ledger" | "keys" | "pricing" | "users" | "channels" | "audit" | "health" | "memory" | "runtime";
  matches?: string[];
};

const navigationItems: NavItem[] = [
  { to: "/", label: "智脑总览", icon: "overview" },
  { to: "/channels", label: "渠道与额度", icon: "channels" },
  { to: "/keys", label: "API Key", icon: "keys" },
  { to: "/usage", label: "用量统计", icon: "ledger", matches: ["/usage", "/ledger"] },
  { to: "/connection", label: "接入", icon: "connection" },
];

function NavGlyph({ name }: { name: NavItem["icon"] }) {
  const paths: Record<NavItem["icon"], React.ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /></>,
    connection: <><path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1" /><path d="M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1" /></>,
    tasks: <><circle cx="12" cy="12" r="9" /><path d="m8.5 12 2.2 2.2 4.8-5" /></>,
    requests: <><path d="M8 6h12M8 12h12M8 18h12" /><path d="M4 6h.01M4 12h.01M4 18h.01" /></>,
    ledger: <><path d="M4 6h14a2 2 0 0 1 2 2v10H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h12" /><path d="M15 11h5v4h-5a2 2 0 0 1 0-4Z" /></>,
    keys: <><circle cx="8" cy="15" r="4" /><path d="m11 12 8-8M15 8l2 2M17 6l2 2" /></>,
    pricing: <><path d="M20 13 13 20l-9-9V4h7Z" /><circle cx="8.5" cy="8.5" r="1" /></>,
    users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
    channels: <><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="8" cy="6" r="2" /><circle cx="16" cy="12" r="2" /><circle cx="10" cy="18" r="2" /></>,
    audit: <><path d="M9 5h6M9 9h6M9 13h4" /><path d="M6 3h12v18H6z" /></>,
    health: <path d="M3 12h4l2-5 4 10 2-5h6" />,
    memory: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>,
    runtime: <><path d="M12 2v10" /><path d="M6.3 5.3a8 8 0 1 0 11.4 0" /></>,
  };
  return <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function isPathActive(pathname: string, to: string) {
  return to === "/" ? pathname === "/" : pathname.startsWith(to);
}

function isRouteActive(pathname: string, item: NavItem) {
  return (item.matches || [item.to]).some((path) => isPathActive(pathname, path));
}

const contextualNavigation = [
  { paths: ["/usage", "/requests", "/tasks", "/audit"], items: [["用量", "/usage"], ["调用明细", "/requests"]] },
  { paths: ["/memory", "/pricing", "/users", "/runtime", "/health"], items: [["设置", "/memory"], ["健康", "/health"]] },
] as const;

function ThemeButton() {
  const { mounted, resolvedTheme, setTheme } = useTheme();
  const isDark = mounted && resolvedTheme === "dark";

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={isDark ? "切换到亮色主题" : "切换到深色主题"}
      onClick={() => setTheme(isDark ? "light" : "dark")}
    >
      <span aria-hidden="true">{isDark ? "☀" : "◐"}</span>
    </Button>
  );
}

function AppNavigation({
  pathname,
  open,
  onNavigate,
}: {
  pathname: string;
  open: boolean;
  onNavigate: () => void;
}) {
  return (
    <aside className={`sidebar${open ? " sidebar-open" : ""}`}>
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden="true">
          A
        </span>
        <div className="brand-copy">
          <strong>AI Gateway</strong>
        </div>
      </div>

      <div className="sidebar-scroll">
        <Navigation
          orientation="vertical"
          variant="pill"
          size="md"
          activeLink={navigationItems.find((item) => isRouteActive(pathname, item))?.to ?? null}
        >
          <NavigationList>
            {navigationItems.map((item) => {
                  const active = isRouteActive(pathname, item);
                  return (
                    <NavigationItem key={item.to}>
                      <NavigationLink
                        value={item.to}
                        active={active}
                        render={<NavLink to={item.to} end={item.to === "/"} onClick={onNavigate} />}
                      >
                        <NavGlyph name={item.icon} />
                        <strong className="nav-label">{item.label}</strong>
                      </NavigationLink>
                    </NavigationItem>
                  );
            })}
          </NavigationList>
        </Navigation>
      </div>

      <div className="sidebar-footer">
        <div className="endpoint-card">
          <span className="endpoint-status" aria-hidden="true" />
          <div>
            <strong>统一 API</strong>
            <code>127.0.0.1:4000/v1</code>
          </div>
        </div>
      </div>
    </aside>
  );
}

function AuthenticatedApp() {
  const location = useLocation();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const desktopMode = document.documentElement.classList.contains("desktop-app");
  useEffect(() => {
    document.body.classList.add("dashboard-active");
    return () => document.body.classList.remove("dashboard-active");
  }, []);
  const contextLinks = contextualNavigation.find((group) => group.paths.some((path) => isPathActive(location.pathname, path)))?.items;
  return (
    <BackgroundPattern
      variant="dots"
      cellSize={22}
      spotlight={desktopMode ? false : { size: 420, persistent: true }}
      track={desktopMode ? "self" : "window"}
      className="app-pattern"
    >
      <div className="app-shell">
        <AppNavigation
          pathname={location.pathname}
          open={navigationOpen}
          onNavigate={() => setNavigationOpen(false)}
        />
        {navigationOpen ? (
          <button
            className="sidebar-scrim"
            aria-label="关闭导航"
            onClick={() => setNavigationOpen(false)}
          />
        ) : null}

        <div className="workspace">
          <header className="workspace-bar">
            <div className="workspace-leading">
              <Button
                className="mobile-nav-button"
                variant="ghost"
                size="icon-sm"
                aria-label="打开导航"
                onClick={() => setNavigationOpen(true)}
              >
                <span aria-hidden="true">☰</span>
              </Button>
              {contextLinks ? (
                <nav className="context-nav" aria-label="当前功能分类">
                  {contextLinks.map(([label, to]) => (
                    <NavLink key={to} to={to} className={({ isActive }) => isActive ? "context-link context-link-active" : "context-link"}>{label}</NavLink>
                  ))}
                </nav>
              ) : null}
            </div>
            <div className="workspace-actions">
              <span className="topbar-status" title="本地服务正常" aria-label="本地服务正常">
                <span className="live-dot" aria-hidden="true" />
              </span>
              <NavLink className={`topbar-shortcut${["/requests", "/tasks", "/audit"].some((path) => isPathActive(location.pathname, path)) ? " topbar-shortcut-active" : ""}`} to="/requests" title="分析" aria-label="分析">
                <NavGlyph name="requests" />
              </NavLink>
              <NavLink className={`topbar-shortcut${["/memory", "/pricing", "/users", "/runtime", "/health"].some((path) => isPathActive(location.pathname, path)) ? " topbar-shortcut-active" : ""}`} to="/memory" title="设置" aria-label="设置">
                <NavGlyph name="memory" />
              </NavLink>
              <ThemeButton />
            </div>
          </header>

          <main className="main">
            <Suspense fallback={<div className="route-loading"><Skeleton className="route-loading-title" /><Skeleton className="route-loading-panel" /></div>}>
              <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/connection" element={<ConnectionPage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/tasks/:id" element={<TaskDetailPage />} />
              <Route path="/requests" element={<RequestsPage />} />
              <Route path="/requests/:id" element={<RequestDetailPage />} />
              <Route path="/usage" element={<UsagePage />} />
              <Route path="/ledger" element={<Navigate to="/usage" replace />} />
              <Route path="/keys" element={<KeysPage />} />
              <Route path="/pricing" element={<PricingPage />} />
              <Route path="/users" element={<UsersPage />} />
              <Route path="/channels" element={<ChannelsPage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="/health" element={<HealthPage />} />
              <Route path="/memory" element={<MemoryPage />} />
              <Route path="/runtime" element={<RuntimePage />} />
              <Route path="/login" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </main>
        </div>
      </div>
    </BackgroundPattern>
  );
}

function DashboardTokenGate({ onSaved }: { onSaved: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    api.setDashboardToken(token);
    try { await api.authStatus(); onSaved(); } catch (caught) { window.sessionStorage.removeItem("dashboardToken"); setError((caught as Error).message); }
  };
  return <BackgroundPattern variant="dots" spotlight={{ size: 500, persistent: true }} track="window" className="login-shell"><section className="card login-card"><div className="login-brand"><span className="brand-mark">P</span><Badge variant="primary-outline">安全访问</Badge></div><h2>输入仪表盘令牌</h2><form onSubmit={submit}><label htmlFor="dashboard-token">仪表盘令牌</label><Input id="dashboard-token" type="password" value={token} onChange={(event) => setToken(event.target.value)} required />{error ? <Alert variant="error"><AlertDescription>{error}</AlertDescription></Alert> : null}<Button className="login-submit" type="submit" disabled={!token.trim()}>进入控制台</Button></form></section></BackgroundPattern>;
}

export default function App() {
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.authStatus,
    retry: false,
  });

  if (status.isLoading) {
    return (
      <div className="app-loading" aria-label="正在加载控制台">
        <Skeleton className="app-loading-mark" />
        <Skeleton className="app-loading-line" />
      </div>
    );
  }

  const authed = status.data?.authenticated || status.data?.auth_mode === "local";

  if (!authed && status.data?.auth_mode === "token") {
    return <DashboardTokenGate onSaved={() => status.refetch()} />;
  }

  if (!authed && status.data?.auth_mode === "accounts") {
    return (
      <Routes>
        <Route
          path="/login"
          element={<LoginPage hasUsers={!!status.data?.has_users} />}
        />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return <AuthenticatedApp />;
}
