import React, { useEffect, useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Building2,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Cloud,
  Database,
  FileSpreadsheet,
  FolderSync,
  LayoutDashboard,
  Layers,
  LoaderCircle,
  LogOut,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  TrendingUp,
  Users,
  X,
  Menu,
  AlertCircle,
  CalendarDays,
  KeyRound,
  Pencil,
} from "lucide-react";
import {
  AreaChart,
  Area,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
  ResponsiveContainer,
} from "recharts";
import "./style.css";
import Operations from "./Operations";
import DiscountPolicyPreview from "./DiscountPolicyPreview";
import SubscriptionConsumption from "./SubscriptionConsumption";
import SubscriptionTier from "./SubscriptionTier";
import ResourceDetails from "./ResourceDetails";
import { previewDiscount } from "./discountPolicy";

type Row = Record<string, any>;
const money = (v: any) =>
  v === null || v === undefined
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2,
      }).format(Number(v));
const costMoney = (v: any, currency: string) => {
  if (v === null || v === undefined) return "—";
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(v));
  } catch {
    return currency + " " + Number(v).toFixed(2);
  }
};

const utcDay = (d = new Date()) => d.toISOString().slice(0, 10);
const yesterday = () => utcDay(new Date(Date.now() - 86400000));
const monthStart = () => utcDay().slice(0, 7) + "-01";
async function api(path: string, options: RequestInit = {}) {
  const res = await fetch("/api/v1" + path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? options.headers
        : { "Content-Type": "application/json", ...options.headers },
  });
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event("acr:unauthorized"));
    let body;
    try {
      body = await res.json();
    } catch {
      throw new Error("服务暂不可用，请重试");
    }
    const detail = body.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message || "输入有误，请检查表单",
    );
  }
  return res.json();
}
const nav = [
  { id: "dashboard", label: "数据总览", icon: LayoutDashboard },
  { id: "customers", label: "客户管理", icon: Users },
  { id: "tenants", label: "租户管理", icon: Building2 },
  { id: "subscriptions", label: "订阅管理", icon: Layers },
  { id: "resources", label: "资源详情", icon: Database },
  { id: "connections", label: "Azure 接入", icon: Cloud },
  { id: "alerts", label: "告警中心", icon: AlertCircle },
  { id: "reports", label: "报表中心", icon: FileSpreadsheet },
  { id: "governance", label: "归属与删除", icon: ShieldCheck },
  { id: "settings", label: "系统设置", icon: Settings2 },
];
const settingsTabs = [
  { id: "general", label: "基础设置", icon: Settings2 },
  { id: "imports", label: "数据导入", icon: FileSpreadsheet },
  { id: "jobs", label: "同步中心", icon: FolderSync },
];
function Badge({
  children,
  tone = "gray",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return (
    <span className={"badge " + tone}>
      <i />
      {children}
    </span>
  );
}
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: React.ReactNode;
  close: () => void;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);
  return (
    <div
      className="modal-wrap"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <button className="icon-button" onClick={close} aria-label="关闭">
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
function Login({ onLogin }: { onLogin: (u: Row) => void }) {
  const [ssoEnabled, setSsoEnabled] = useState(false);
  useEffect(() => {
    api("/auth/providers")
      .then((p) => setSsoEnabled(p.sso))
      .catch(() => {});
  }, []);
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <div className="login">
      <div className="login-story">
        <div className="brand">
          <span className="brand-icon">
            <Activity size={25} />
          </span>
          AzureLens
        </div>
        <div>
          <h1>
            让每一份云消费，
            <br />
            都有清晰的答案。
          </h1>
          <p>
            从成本数据到业务洞察。集中管理 Azure 客户，
            <br />
            追踪消费趋势，掌握内部估算 ACR。
          </p>
          <div className="login-visual">
            <div className="visual-head">
              <span>云消费洞察</span>
              <TrendingUp size={20} />
            </div>
            <svg viewBox="0 0 480 130">
              <path
                d="M0 110 L45 98 L90 106 L135 70 L180 80 L225 48 L270 62 L315 24 L360 40 L405 12 L450 18 L480 0"
                fill="none"
                stroke="#83b5ff"
                strokeWidth="3"
              />
              <path
                d="M0 110 L45 98 L90 106 L135 70 L180 80 L225 48 L270 62 L315 24 L360 40 L405 12 L450 18 L480 0 V130 H0Z"
                fill="#ffffff0b"
              />
            </svg>
            <div className="visual-foot">
              <ShieldCheck size={16} /> 可追溯的数据 · 一致的计算口径
            </div>
          </div>
        </div>
      </div>
      <main className="login-main">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              onLogin(
                await api("/auth/login", {
                  method: "POST",
                  body: JSON.stringify({ email, password }),
                }),
              );
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <h2>欢迎回来</h2>
          <Field label="工作邮箱">
            <input
              autoFocus
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@company.com"
              autoComplete="username"
            />
          </Field>
          <Field label="密码">
            <input
              required
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="输入账号密码"
              autoComplete="current-password"
            />
          </Field>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <button disabled={busy} className="primary login-submit">
            {busy ? (
              <LoaderCircle className="spin" size={18} />
            ) : (
              <>
                登录工作空间
                <ArrowRight size={18} />
              </>
            )}
          </button>
          <div className="login-notice">
            <ShieldCheck size={17} />
            <span>仅限已授权员工使用。账号由管理员创建。</span>
          </div>
        </form>
        {ssoEnabled && (
          <a className="text-button mt" href="/api/v1/auth/sso/login">
            使用 Microsoft Entra SSO 登录
          </a>
        )}
      </main>
    </div>
  );
}
function App() {
  const [user, setUser] = useState<Row | null>(null),
    [checking, setChecking] = useState(true),
    [page, setPage] = useState("dashboard"),
    [settingsTab, setSettingsTab] = useState("general"),
    [mobile, setMobile] = useState(false),
    [customers, setCustomers] = useState<Row[]>([]),
    [tenants, setTenants] = useState<Row[]>([]),
    [subs, setSubs] = useState<Row[]>([]),
    [batches, setBatches] = useState<Row[]>([]),
    [fx, setFx] = useState<Row[]>([]),
    [audit, setAudit] = useState<Row[]>([]),
    [dash, setDash] = useState<Row | null>(null),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [loading, setLoading] = useState(false),
    [modal, setModal] = useState(""),
    [edit, setEdit] = useState<Row | null>(null),
    [search, setSearch] = useState(""),
    [scope, setScope] = useState(""),
    [tenantScope, setTenantScope] = useState(""),
    [subScope, setSubScope] = useState(""),
    [start, setStart] = useState(monthStart()),
    [end, setEnd] = useState(yesterday()),
    [period, setPeriod] = useState("month"),
    [revision, setRevision] = useState(0),
    [detail, setDetail] = useState<Row | null>(null);
  useEffect(() => {
    const expire = () => setUser(null);
    window.addEventListener("acr:unauthorized", expire);
    return () => window.removeEventListener("acr:unauthorized", expire);
  }, []);
  const [trendMode, setTrendMode] = useState("day");
  const [metricChoice, setMetricChoice] = useState("auto");
  const [sourceCurrency, setSourceCurrency] = useState("");
  const [acrScope, setAcrScope] = useState("trial");
  const dashboardQuery = useRef("");
  const [catalogPage, setCatalogPage] = useState(1);
  const [catalog, setCatalog] = useState<Row>({ items: [], total: 0 });
  const [catalogLoading, setCatalogLoading] = useState(false);
  useEffect(() => {
    if (!user || !["customers", "tenants", "subscriptions"].includes(page))
      return;
    let active = true;
    setCatalogLoading(true);
    const timer = setTimeout(() => {
      const params = new URLSearchParams({
        page: String(catalogPage),
        page_size: "25",
        search,
        customer_id: scope,
        tenant_id: tenantScope,
      });
      api("/catalog/" + page + "?" + params)
        .then((result) => {
          if (active) setCatalog(result);
        })
        .catch((e) => {
          if (active) setError(e.message);
        })
        .finally(() => {
          if (active) setCatalogLoading(false);
        });
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [user, page, catalogPage, search, scope, tenantScope, revision]);
  useEffect(() => setCatalogPage(1), [page, search, scope, tenantScope]);
  const [mtd, setMtd] = useState<Row | null>(null);
  useEffect(() => {
    if (!user) return;
    api("/dashboard?start=" + monthStart() + "&end=" + yesterday())
      .then(setMtd)
      .catch(() => {});
  }, [user, revision]);
  useEffect(() => {
    if (!user) return;
    const timer = setInterval(() => setRevision((r) => r + 1), 15000);
    return () => clearInterval(timer);
  }, [user]);
  const query = new URLSearchParams({
    start,
    end,
    customer_id: scope,
    tenant_id: tenantScope,
    subscription_id: subScope,
    granularity: trendMode,
  }).toString();
  useEffect(() => {
    api("/auth/me")
      .then(setUser)
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    if (!user) return;
    setLoading(true);
    Promise.all([
      api("/customers"),
      api("/tenants"),
      api("/subscriptions"),
      api("/sync/batches"),
      api("/settings/fx-rates"),
      user.role === "admin" ? api("/audit") : Promise.resolve([]),
    ])
      .then(([c, t, s, b, f, a]) => {
        setCustomers(c);
        setTenants(t);
        setSubs(s);
        setBatches(b);
        setFx(f);
        setAudit(a);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user, revision]);
  useEffect(() => {
    if (!user) return;
    let active = true;
    const key = user.id + ":" + query;
    if (dashboardQuery.current !== key) setDash(null);
    dashboardQuery.current = key;
    api("/dashboard?" + query)
      .then((d) => {
        if (active) setDash(d);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [user, query, revision]);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 4000);
      return () => clearTimeout(t);
    }
  }, [toast]);
  const refresh = () => setRevision((r) => r + 1);
  const customerName = (id: string) =>
    customers.find((c) => c.id === id)?.name || "—";
  const tenantName = (id: string) =>
    tenants.find((t) => t.id === id)?.name || "—";
  const subName = (id: string) => subs.find((s) => s.id === id)?.name || id;
  const open = (name: string, row: Row | null = null) => {
    setEdit(row);
    setError("");
    setModal(name);
  };
  const save = async (path: string, body: Row, method = "POST") => {
    const created = await api(path, { method, body: JSON.stringify(body) });
    if (path === "/customers/onboarding") {
      setScope(created.customer.id);
      setTenantScope(body.tenants[0].id);
      setSubScope("");
      setPage("connections");
    }
    if (path === "/tenants") {
      setScope(body.customer_id);
      setTenantScope(created.id);
      setSubScope("");
      setPage("connections");
    }
    setModal("");
    setToast(
      path.endsWith("/discounts")
        ? `已保存折扣版本：系数 ${body.factor}，${body.effective_date}（UTC）起生效。已有历史版本保留，相关计算视图将刷新。`
        : "已保存",
    );
    refresh();
  };
  const periodChange = (value: string) => {
    setPeriod(value);
    let d = new Date();
    let e = new Date(Date.now() - 86400000);
    if (value === "month") setStart(monthStart());
    else if (value === "last") {
      d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 0));
      e = d;
      setStart(
        utcDay(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1))),
      );
    } else if (value !== "custom")
      setStart(utcDay(new Date(e.getTime() - (Number(value) - 1) * 86400000)));
    setEnd(utcDay(e));
  };
  if (checking)
    return (
      <div className="boot">
        <LoaderCircle className="spin" />
        正在载入工作空间
      </div>
    );
  if (!user) return <Login onLogin={setUser} />;
  const sourceGroups: Row[] = dash?.raw?.currencies || [];
  const sourceGroup =
    sourceGroups.find((g) => g.currency === sourceCurrency) ||
    sourceGroups.find((g) => g.currency === "USD" && g.records > 0) ||
    sourceGroups.find((g) => g.records > 0) ||
    sourceGroups[0];
  const sourceMode =
    metricChoice === "source" ||
    (metricChoice === "auto" &&
      dash?.total == null &&
      dash?.estimate?.total == null &&
      dash?.estimate?.lifetime == null &&
      sourceGroups.length > 0);
  const trialMode =
    !sourceMode &&
    acrScope === "trial" &&
    dash?.estimate?.status === "UNVERIFIED";
  const acrData = trialMode ? dash?.estimate : dash;
  const chartCurrency = sourceMode ? sourceGroup?.currency || "USD" : "USD";
  const chartTrend: Row[] = sourceMode
    ? sourceGroup?.trend || []
    : acrData?.trend || [];
  const chartTop: Row[] = sourceMode
    ? sourceGroup?.top || []
    : acrData?.top || [];
  const chartName = sourceMode ? "原始成本" : "ACR";
  const chartMoney = (v: any) => costMoney(v, chartCurrency);
  const catalogRows: Row[] = catalog.items
    .map((row: Row) =>
      (page === "customers"
        ? customers
        : page === "tenants"
          ? tenants
          : subs
      ).find((x) => x.id === row.id),
    )
    .filter((row: Row | undefined): row is Row => Boolean(row));
  const filteredCustomers = page === "customers" ? catalogRows : [];
  return (
    <div className="app">
      <aside className={mobile ? "sidebar visible" : "sidebar"}>
        <div className="brand">
          <span className="brand-icon">
            <Activity size={23} />
          </span>
          AzureLens
        </div>
        <div className="workspace">
          <span className="workspace-icon">P</span>
          <div>
            <strong>Partner 工作空间</strong>
          </div>
          <ChevronDown size={15} />
        </div>
        <div className="nav-label">工作空间</div>
        <nav>
          {nav.map((n) => (
            <button
              key={n.id}
              onClick={() => {
                setPage(n.id);
                if (n.id === "settings") setSettingsTab("general");
                setSearch("");
                setMobile(false);
              }}
              className={page === n.id ? "active" : ""}
            >
              <n.icon size={19} />
              {n.label}
              {page === n.id && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="side-bottom">
          <div className="connection">
            <span className="connection-icon">
              <Cloud size={20} />
            </span>
            <div>
              <strong>Azure 数据连接</strong>
            </div>
            <span className="status-dot" />
          </div>
          <div className="user">
            <span className="avatar">{user.name.slice(0, 1)}</span>
            <div>
              <strong>{user.name}</strong>
              <small>
                {user.role === "admin" ? "工作空间管理员" : "内部成员"}
              </small>
            </div>
            <button
              aria-label="退出登录"
              className="icon-button"
              onClick={async () => {
                await api("/auth/logout", { method: "POST" });
                setUser(null);
              }}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="body">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-toggle"
              onClick={() => setMobile(!mobile)}
              aria-label="导航菜单"
            >
              <Menu size={20} />
            </button>
            工作空间
            <ChevronRight size={14} />
            <strong>{nav.find((n) => n.id === page)?.label}</strong>
          </div>
          <div className="top-right">
            <span className="badge gray">UTC 统计口径</span>
            <button
              className="icon-button"
              aria-label="平台说明"
              onClick={() => open("help")}
            >
              <CircleHelp size={19} />
            </button>
            <span className="avatar small">{user.name.slice(0, 1)}</span>
          </div>
        </header>
        <main className="content">
          {user.email === "demo@azure-lens.local" && (
            <div className="notice">
              <AlertCircle size={16} />
              演示工作空间：所有预置客户和金额均为合成数据，未连接
              Azure。请勿用于业务对账。
            </div>
          )}
          <div className="page-title">
            <div>
              <h1>{nav.find((n) => n.id === page)?.label}</h1>
            </div>
            <div className="title-actions">
              {page === "dashboard" ? (
                <button
                  className="secondary"
                  onClick={() =>
                    window.location.assign("/api/v1/reports/export?" + query)
                  }
                >
                  <ArrowDownToLine size={16} />
                  {trialMode ? "导出已核验报表" : "导出报表"}
                </button>
              ) : page === "customers" ? (
                <button className="primary" onClick={() => open("customer")}>
                  <Plus size={17} />
                  新建客户
                </button>
              ) : page === "tenants" ? (
                <button className="primary" onClick={() => open("tenant")}>
                  <Plus size={17} />
                  添加租户
                </button>
              ) : page === "subscriptions" ? (
                <button
                  className="primary"
                  onClick={() => open("subscription")}
                >
                  <Plus size={17} />
                  添加订阅
                </button>
              ) : page === "settings" && settingsTab === "imports" ? (
                <button
                  className="primary"
                  disabled={!subs.length}
                  onClick={() => open("import")}
                >
                  <Plus size={17} />
                  导入 CSV
                </button>
              ) : (
                page === "settings" &&
                settingsTab === "general" &&
                user.role === "admin" && (
                  <button className="primary" onClick={() => open("fx")}>
                    <Plus size={17} />
                    新增汇率
                  </button>
                )
              )}
            </div>
          </div>
          {page === "settings" && (
            <div
              className="settings-tabs"
              role="tablist"
              aria-label="系统设置分组"
            >
              {settingsTabs.map((tab, index) => (
                <button
                  key={tab.id}
                  id={"settings-tab-" + tab.id}
                  type="button"
                  role="tab"
                  aria-selected={settingsTab === tab.id}
                  aria-controls={"settings-panel-" + tab.id}
                  tabIndex={settingsTab === tab.id ? 0 : -1}
                  onClick={() => setSettingsTab(tab.id)}
                  onKeyDown={(event) => {
                    const target =
                      event.key === "ArrowRight"
                        ? (index + 1) % settingsTabs.length
                        : event.key === "ArrowLeft"
                          ? (index + settingsTabs.length - 1) %
                            settingsTabs.length
                          : event.key === "Home"
                            ? 0
                            : event.key === "End"
                              ? settingsTabs.length - 1
                              : -1;
                    if (target < 0) return;
                    event.preventDefault();
                    setSettingsTab(settingsTabs[target].id);
                    document
                      .getElementById("settings-tab-" + settingsTabs[target].id)
                      ?.focus();
                  }}
                >
                  <tab.icon size={16} />
                  {tab.label}
                </button>
              ))}
            </div>
          )}
          {error && !modal && (
            <div role="alert" className="error">
              {error}
              <button onClick={() => setError("")} className="icon-button">
                <X size={16} />
              </button>
            </div>
          )}
          {page === "dashboard" && (
            <>
              <div className="filters">
                <div className="filter-group">
                  <CalendarDays size={16} />
                  <select
                    aria-label="日期范围"
                    value={period}
                    onChange={(e) => periodChange(e.target.value)}
                  >
                    <option value="month">本月 · 截至昨日</option>
                    <option value="last">上月</option>
                    <option value="7">近 7 天</option>
                    <option value="30">近 30 天</option>
                    <option value="90">近 90 天</option>
                    <option value="custom">自定义日期</option>
                  </select>
                </div>
                {period === "custom" ? (
                  <div className="date-inputs">
                    <input
                      aria-label="开始日期"
                      type="date"
                      value={start}
                      onChange={(e) => setStart(e.target.value)}
                    />
                    <span>—</span>
                    <input
                      aria-label="结束日期"
                      type="date"
                      value={end}
                      onChange={(e) => setEnd(e.target.value)}
                    />
                  </div>
                ) : (
                  <span className="date-label">
                    {start.replaceAll("-", ".")} — {end.replaceAll("-", ".")}
                  </span>
                )}
                <select
                  aria-label="客户筛选"
                  value={scope}
                  onChange={(e) => {
                    setScope(e.target.value);
                    setTenantScope("");
                    setSubScope("");
                  }}
                >
                  <option value="">全部客户</option>
                  {customers.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="租户筛选"
                  value={tenantScope}
                  onChange={(e) => {
                    setTenantScope(e.target.value);
                    setSubScope("");
                  }}
                >
                  <option value="">全部租户</option>
                  {tenants
                    .filter((t) => !scope || t.customer_id === scope)
                    .map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                </select>
                <select
                  aria-label="订阅筛选"
                  value={subScope}
                  onChange={(e) => setSubScope(e.target.value)}
                >
                  <option value="">全部订阅</option>
                  {subs
                    .filter(
                      (s) =>
                        (!scope || s.customer_id === scope) &&
                        (!tenantScope || s.tenant_id === tenantScope),
                    )
                    .map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                </select>
              </div>
              <div className="metric-toolbar">
                <div
                  className="metric-switch"
                  role="group"
                  aria-label="金额口径"
                >
                  <button
                    aria-pressed={sourceMode}
                    onClick={() => setMetricChoice("source")}
                  >
                    原始成本
                  </button>
                  <button
                    aria-pressed={!sourceMode}
                    onClick={() => setMetricChoice("acr")}
                  >
                    内部 ACR
                  </button>
                </div>
                {sourceMode && sourceGroups.length > 0 && (
                  <label className="metric-currency">
                    原币币种{" "}
                    <select
                      aria-label="原始成本币种"
                      value={sourceGroup?.currency || ""}
                      onChange={(e) => setSourceCurrency(e.target.value)}
                    >
                      {sourceGroups.map((g) => (
                        <option key={g.currency} value={g.currency}>
                          {g.currency}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {!sourceMode && (
                  <label className="metric-currency">
                    核验口径{" "}
                    <select
                      aria-label="ACR 核验口径"
                      value={acrScope}
                      onChange={(e) => setAcrScope(e.target.value)}
                    >
                      <option value="trial">全部可计算费用</option>
                      <option value="verified">仅已核验</option>
                    </select>
                  </label>
                )}
              </div>
              <div className="stats">
                <Stat
                  label={
                    sourceMode ? `区间原始成本 · ${chartCurrency}` : "区间 ACR"
                  }
                  value={
                    sourceMode
                      ? chartMoney(sourceGroup?.total)
                      : money(acrData?.total)
                  }
                  icon={TrendingUp}
                  primary
                />
                <Stat
                  label={
                    sourceMode
                      ? `历史累计原始成本 · ${chartCurrency}`
                      : "历史累计 ACR"
                  }
                  value={
                    sourceMode
                      ? chartMoney(sourceGroup?.lifetime)
                      : money(acrData?.lifetime)
                  }
                  icon={Database}
                />
                <Stat
                  label="客户总数"
                  value={dash ? String(dash.customers) : "—"}
                  icon={Users}
                />

                <Stat
                  label="同步异常对象"
                  value={String(
                    tenants.filter(
                      (t) =>
                        (!scope || t.customer_id === scope) &&
                        (t.auth_status === "FAILED" ||
                          t.sync_status === "FAILED"),
                    ).length +
                      subs.filter(
                        (s) =>
                          (!scope || s.customer_id === scope) &&
                          (!tenantScope || s.tenant_id === tenantScope) &&
                          s.access_status === "FAILED",
                      ).length,
                  )}
                  icon={AlertCircle}
                />
                <Stat
                  label="已核验 ACR 月份环比"
                  value={
                    dash?.comparison == null
                      ? "不可比"
                      : Number(dash.comparison).toFixed(1) + "%"
                  }
                  icon={TrendingUp}
                />
              </div>
              <div className="chart-grid">
                <section className="panel trend-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>
                        消费趋势{" "}
                        <span className="tiny-tag">{chartCurrency}</span>
                      </h2>
                    </div>
                    <select
                      aria-label="趋势粒度"
                      value={trendMode}
                      onChange={(e) => setTrendMode(e.target.value)}
                    >
                      <option value="day">按日</option>
                      <option value="month">按月</option>
                    </select>
                    <span className="legend">
                      <i />
                      {chartName}
                    </span>
                  </div>
                  <div className="chart">
                    {chartTrend.some((p: Row) => p.value !== null) ? (
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart
                          data={chartTrend.map((p: Row) => ({
                            ...p,
                            value: p.value === null ? null : Number(p.value),
                          }))}
                          margin={{ left: 0, right: 12, top: 15, bottom: 0 }}
                        >
                          <defs>
                            <linearGradient
                              id="area"
                              x1="0"
                              y1="0"
                              x2="0"
                              y2="1"
                            >
                              <stop
                                offset="0%"
                                stopColor="#377cf6"
                                stopOpacity={0.22}
                              />
                              <stop
                                offset="100%"
                                stopColor="#377cf6"
                                stopOpacity={0}
                              />
                            </linearGradient>
                          </defs>
                          <CartesianGrid
                            strokeDasharray="4 4"
                            vertical={false}
                            stroke="#e9edf4"
                          />
                          <XAxis
                            dataKey="day"
                            tickFormatter={(v) => v.slice(5).replace("-", "/")}
                            tick={{ fontSize: 11, fill: "#8b95a7" }}
                            axisLine={false}
                            tickLine={false}
                            minTickGap={35}
                          />
                          <YAxis
                            tickFormatter={(v) =>
                              Math.abs(v) >= 1000
                                ? `${(v / 1000).toFixed(1)}k`
                                : String(v)
                            }
                            tick={{ fontSize: 11, fill: "#8b95a7" }}
                            axisLine={false}
                            tickLine={false}
                          />
                          <Tooltip
                            formatter={(value: any) => [
                              chartMoney(value),
                              chartName,
                            ]}
                            contentStyle={{
                              borderRadius: 10,
                              border: "1px solid #e7ebf2",
                              fontSize: 12,
                            }}
                          />
                          <Area
                            isAnimationActive={false}
                            type="monotone"
                            dataKey="value"
                            stroke="#377cf6"
                            fill="url(#area)"
                            strokeWidth={2.5}
                            dot={{
                              r: 3,
                              fill: "#377cf6",
                              stroke: "#fff",
                              strokeWidth: 1.5,
                            }}
                            connectNulls={false}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    ) : (
                      <Empty
                        title={
                          dash
                            ? sourceMode
                              ? "当前币种在所选区间暂无费用"
                              : trialMode
                                ? "当前区间暂无 ACR 数据"
                                : "当前没有可计入 ACR 的费用"
                            : "正在加载趋势"
                        }
                        text={
                          sourceMode
                            ? "可调整日期、币种或对象筛选；也可前往系统设置的同步中心查看采集进度。"
                            : dash?.raw?.records > 0
                              ? "已有原始费用，切换“原始成本”即可查看；完成口径、折扣和汇率核验后可计算 ACR。"
                              : "可通过 Azure 自动同步或 CSV 导入成本。"
                        }
                      />
                    )}
                  </div>
                </section>
                <section className="panel top-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Top 5 客户</h2>
                    </div>
                    <TrendingUp size={19} className="muted" />
                  </div>
                  {chartTop.length ? (
                    <div className="rank-list">
                      {chartTop.map((c: Row, i: number) => (
                        <button
                          className="rank-item"
                          key={c.id}
                          onClick={() => {
                            setScope(c.id);
                            setTenantScope("");
                            setSubScope("");
                          }}
                        >
                          <span className={"rank " + (i === 0 ? "first" : "")}>
                            {String(i + 1).padStart(2, "0")}
                          </span>
                          <div className="rank-data">
                            <div>
                              <strong>{c.name}</strong>
                              <b>{chartMoney(c.value)}</b>
                            </div>
                            <div className="bar">
                              <i
                                style={{
                                  width: `${Math.max(2, (Number(c.value) / Math.max(1, Number(chartTop[0].value))) * 100)}%`,
                                }}
                              />
                            </div>
                          </div>
                          <ChevronRight size={14} />
                        </button>
                      ))}
                    </div>
                  ) : (
                    <Empty
                      title="暂无排名"
                      text={
                        sourceMode
                          ? "当前筛选范围暂无费用。"
                          : trialMode
                            ? "当前缺少可计算的折扣或汇率版本。"
                            : "仅对已核验的 ACR 进行排名；已有原始成本可切换查看。"
                      }
                    />
                  )}
                  <div className="panel-footer">
                    <button
                      className="text-button"
                      onClick={() => setPage("customers")}
                    >
                      查看全部客户
                      <ArrowRight size={14} />
                    </button>
                  </div>
                </section>
              </div>
              {sourceMode && sourceGroup?.services?.length > 0 && (
                <section className="panel source-services">
                  <div className="panel-heading">
                    <div>
                      <h2>Azure 服务成本</h2>
                    </div>
                    <Badge tone="blue">
                      {sourceGroup.services.length} 个服务
                    </Badge>
                  </div>
                  <div className="source-service-grid">
                    {sourceGroup.services.slice(0, 12).map((service: Row) => (
                      <div className="source-service" key={service.name}>
                        <span>{service.name}</span>
                        <strong>{chartMoney(service.value)}</strong>
                      </div>
                    ))}
                  </div>
                </section>
              )}
              <SubscriptionConsumption
                start={start}
                end={end}
                customerId={scope}
                tenantId={tenantScope}
                subscriptionId={subScope}
                onResource={(row) => {
                  setScope(row.customer_id);
                  setTenantScope(row.tenant_id);
                  setSubScope(row.subscription_id);
                  setPage("resources");
                }}
              />
            </>
          )}
          {page === "resources" && (
            <ResourceDetails
              customers={customers}
              tenants={tenants}
              subscriptions={subs}
              customerId={scope}
              tenantId={tenantScope}
              subscriptionId={subScope}
              start={start}
              end={end}
              onCustomer={(id) => {
                setScope(id);
                setTenantScope("");
                setSubScope("");
              }}
              onTenant={(id) => {
                setTenantScope(id);
                setSubScope("");
              }}
              onSubscription={setSubScope}
              onStart={(day) => {
                setStart(day);
                setPeriod("custom");
              }}
              onEnd={(day) => {
                setEnd(day);
                setPeriod("custom");
              }}
            />
          )}
          {page === "customers" && (
            <section className="panel">
              <div className="table-toolbar">
                <div className="search">
                  <Search size={17} />
                  <input
                    aria-label="搜索客户"
                    placeholder="搜索客户名称或 ID…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <span className="muted">{catalog.total} 个客户</span>
              </div>
              {filteredCustomers.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>客户名称 / ID</th>
                        <th>当前折扣</th>
                        <th>租户 / 订阅</th>
                        <th>状态</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredCustomers.map((c, i) => (
                        <tr key={c.id}>
                          <td>
                            <button
                              className="entity-link"
                              onClick={() => {
                                setDetail({ ...c, _type: "customer" });
                              }}
                            >
                              <span
                                className={"entity-avatar color-" + (i % 4)}
                              >
                                {c.name[0]}
                              </span>
                              <span>
                                <strong>{c.name}</strong>
                                <small>{c.id.slice(0, 8).toUpperCase()}</small>
                              </span>
                            </button>
                          </td>
                          <td>
                            {c.factor !== null
                              ? `${Number(c.factor) * 100}%`
                              : "未生效"}
                            <small>{c.discounts.length} 个历史版本</small>
                          </td>
                          <td>
                            {c.tenants} <span className="muted">/</span>{" "}
                            {c.subscriptions}
                          </td>
                          <td>
                            <small>
                              当月 ACR {money(mtd?.customer_totals?.[c.id])}
                            </small>
                            <Badge tone={c.active ? "green" : "gray"}>
                              {c.active ? "启用中" : "已停用"}
                            </Badge>
                          </td>
                          <td>
                            <div className="row-actions">
                              <button onClick={() => open("customer-edit", c)}>
                                编辑
                              </button>
                              <button onClick={() => open("discount", c)}>
                                折扣
                              </button>
                              <button
                                onClick={() =>
                                  open("status", { ...c, kind: "customers" })
                                }
                              >
                                {c.active ? "停用" : "启用"}
                              </button>
                              <button
                                onClick={() => {
                                  setScope(c.id);
                                  setTenantScope("");
                                  setSubScope("");
                                  setPage("dashboard");
                                }}
                              >
                                消费
                                <ArrowUpRight size={12} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty
                  title={search ? "没有匹配的客户" : "从第一位客户开始"}
                  text="新建客户后，可添加租户与订阅，并导入消费数据。"
                />
              )}
            </section>
          )}
          {page === "tenants" && (
            <>
              <section className="panel">
                <div className="table-toolbar">
                  <div className="search">
                    <Search size={17} />
                    <input
                      aria-label="搜索租户"
                      placeholder="搜索租户名称或 Tenant ID…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </div>
                  <select
                    aria-label="租户客户筛选"
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                  >
                    <option value="">全部客户</option>
                    {customers.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </div>
                {catalogRows.length ? (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>租户 / Tenant ID</th>
                          <th>所属客户</th>
                          <th>SP 配置</th>
                          <th>状态</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {catalogRows.map((t) => (
                          <tr key={t.id}>
                            <td>
                              <strong>{t.name}</strong>
                              <small className="mono">{t.id}</small>
                            </td>
                            <td>{customerName(t.customer_id)}</td>
                            <td>
                              <Badge tone="amber">
                                {t.credential_configured
                                  ? t.auth_status === "SUCCEEDED"
                                    ? "认证成功"
                                    : "已配置 · 待校验"
                                  : "待配置"}
                              </Badge>
                            </td>
                            <td>
                              <Badge tone={t.active ? "green" : "gray"}>
                                {t.active ? "启用" : "已停用"}
                              </Badge>
                            </td>
                            <td>
                              <div className="row-actions">
                                <button
                                  onClick={() =>
                                    open("entity-edit", {
                                      ...t,
                                      kind: "tenants",
                                    })
                                  }
                                >
                                  编辑
                                </button>
                                <button
                                  onClick={() => {
                                    setTenantScope(t.id);
                                    setPage("connections");
                                  }}
                                >
                                  验证 / 同步
                                </button>
                                {user.role === "admin" && (
                                  <button onClick={() => open("credential", t)}>
                                    更新凭据
                                  </button>
                                )}
                                <button
                                  onClick={() =>
                                    open("status", { ...t, kind: "tenants" })
                                  }
                                >
                                  {t.active ? "停用" : "启用"}
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <Empty
                    title="暂无匹配的租户"
                    text="选择客户并添加 Azure Tenant，稍后可配置服务主体。"
                  />
                )}
              </section>
            </>
          )}
          {page === "subscriptions" && (
            <section className="panel">
              <div className="table-toolbar">
                <div className="search">
                  <Search size={17} />
                  <input
                    aria-label="搜索订阅"
                    placeholder="搜索订阅名称或 ID…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <select
                  aria-label="订阅客户筛选"
                  value={scope}
                  onChange={(e) => {
                    setScope(e.target.value);
                    setTenantScope("");
                  }}
                >
                  <option value="">全部客户</option>
                  {customers.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="订阅租户筛选"
                  value={tenantScope}
                  onChange={(e) => setTenantScope(e.target.value)}
                >
                  <option value="">全部租户</option>
                  {tenants
                    .filter((t) => !scope || t.customer_id === scope)
                    .map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                </select>
              </div>
              {catalogRows.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>订阅 / Subscription ID</th>
                        <th>当前层级</th>
                        <th>所属客户 / 租户</th>
                        <th>权限状态</th>
                        <th>状态</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {catalogRows.map((s) => (
                        <tr key={s.id}>
                          <td>
                            <strong>{s.name}</strong>
                            <small className="mono">{s.id}</small>
                          </td>
                          <td>
                            <SubscriptionTier
                              subscription={s}
                              onRefresh={refresh}
                              onError={setError}
                            />
                          </td>
                          <td>
                            {customerName(s.customer_id)}
                            <small>{tenantName(s.tenant_id)}</small>
                          </td>
                          <td>
                            <Badge
                              tone={
                                s.access_status === "SUCCEEDED"
                                  ? "green"
                                  : "amber"
                              }
                            >
                              {s.access_status === "SUCCEEDED"
                                ? "成本可读"
                                : s.archived
                                  ? "历史归档"
                                  : s.access_status === "FAILED"
                                    ? "读取失败"
                                    : "待校验"}
                            </Badge>
                          </td>
                          <td>
                            <small>
                              当月 ACR {money(mtd?.subscription_totals?.[s.id])}
                            </small>
                            <Badge tone={s.active ? "green" : "gray"}>
                              {s.active ? "启用" : "已停用"}
                            </Badge>
                          </td>
                          <td>
                            <div className="row-actions">
                              <button
                                onClick={async () => {
                                  try {
                                    await api(
                                      "/subscriptions/" + s.id + "/verify",
                                      { method: "POST" },
                                    );
                                    setToast("校验与同步任务已排队");
                                    refresh();
                                  } catch (e) {
                                    setError((e as Error).message);
                                  }
                                }}
                              >
                                验证 / 同步
                              </button>
                              <button
                                onClick={() =>
                                  open("entity-edit", {
                                    ...s,
                                    kind: "subscriptions",
                                  })
                                }
                              >
                                编辑
                              </button>
                              <button
                                onClick={() =>
                                  open("status", {
                                    ...s,
                                    kind: "subscriptions",
                                  })
                                }
                              >
                                {s.active ? "停用" : "启用"}
                              </button>
                              <button
                                onClick={() => {
                                  setScope(s.customer_id);
                                  setTenantScope(s.tenant_id);
                                  setSubScope(s.id);
                                  setPage("dashboard");
                                }}
                              >
                                消费
                                <ArrowUpRight size={12} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty
                  title="暂无匹配的订阅"
                  text="先添加租户，再手动录入 Subscription ID。"
                />
              )}
            </section>
          )}
          {page === "settings" && settingsTab === "imports" && (
            <div
              id="settings-panel-imports"
              role="tabpanel"
              aria-labelledby="settings-tab-imports"
            >
              <div className="import-hero">
                <span className="import-hero-icon">
                  <FileSpreadsheet size={30} />
                </span>
                <div>
                  <h2>CSV 成本导入</h2>
                </div>
                <a className="secondary" href="/api/v1/sync/template">
                  <ArrowDownToLine size={16} />
                  下载 CSV 模板
                </a>
              </div>

              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>导入历史</h2>
                  </div>
                  <span className="tiny-tag">最近 100 批</span>
                </div>
                <BatchTable
                  batches={batches}
                  subName={subName}
                  onDetail={setDetail}
                />
              </section>
            </div>
          )}
          {page === "settings" && settingsTab === "general" && (
            <div
              id="settings-panel-general"
              role="tabpanel"
              aria-labelledby="settings-tab-general"
            >
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>USD 汇率版本</h2>
                  </div>
                  <Badge tone="blue">手工维护</Badge>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>原币</th>
                        <th>目标币</th>
                        <th>换算系数</th>
                        <th>生效 UTC 日期</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>USD</td>
                        <td>USD</td>
                        <td className="mono">1</td>
                        <td>固定汇率</td>
                      </tr>
                      {fx.map((f) => (
                        <tr key={f.id}>
                          <td>{f.currency}</td>
                          <td>USD</td>
                          <td className="mono">{f.rate}</td>
                          <td>{f.day}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              {user.role === "admin" && (
                <section className="panel mt">
                  <div className="panel-heading">
                    <div>
                      <h2>操作审计</h2>
                    </div>
                    <ShieldCheck size={19} className="muted" />
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>时间</th>
                          <th>操作者</th>
                          <th>操作</th>
                          <th>对象</th>
                        </tr>
                      </thead>
                      <tbody>
                        {audit.map((a) => (
                          <tr key={a.id}>
                            <td>
                              {new Date(a.created).toLocaleString("zh-CN")}
                            </td>
                            <td>{a.actor}</td>
                            <td>
                              <span className="tiny-tag">{a.action}</span>
                            </td>
                            <td className="mono">{a.target.slice(0, 36)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
              {user.role === "admin" && (
                <Operations mode="settings" user={user} />
              )}
            </div>
          )}
          {["customers", "tenants", "subscriptions"].includes(page) &&
            (() => {
              const count = catalog.total;
              const pages = Math.max(1, Math.ceil(count / 25));
              return (
                <div className="panel-footer mt" aria-busy={catalogLoading}>
                  <span>
                    {catalogLoading
                      ? "正在查询…"
                      : `共 ${count} 条 · 每页 25 条`}
                  </span>
                  <div className="row-actions">
                    <button
                      disabled={catalogLoading || catalogPage <= 1}
                      onClick={() => setCatalogPage((p) => p - 1)}
                    >
                      上一页
                    </button>
                    <span>
                      {catalogPage} / {pages}
                    </span>
                    <button
                      disabled={catalogLoading || catalogPage >= pages}
                      onClick={() => setCatalogPage((p) => p + 1)}
                    >
                      下一页
                    </button>
                  </div>
                </div>
              );
            })()}
          {["alerts", "reports", "connections", "governance"].includes(
            page,
          ) && (
            <Operations mode={page} user={user} initialTenant={tenantScope} />
          )}
          {page === "settings" && settingsTab === "jobs" && (
            <div
              id="settings-panel-jobs"
              role="tabpanel"
              aria-labelledby="settings-tab-jobs"
            >
              <Operations mode="jobs" user={user} initialTenant={tenantScope} />
            </div>
          )}
          {loading && page !== "dashboard" && (
            <div className="loading">
              <LoaderCircle size={16} className="spin" />
              正在刷新数据
            </div>
          )}
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Check size={17} />
          {toast}
        </div>
      )}
      {modal && (
        <Modal
          title={
            (
              {
                customer: "新建客户",
                "customer-edit": "编辑客户",
                tenant: "添加租户",
                subscription: "添加订阅",
                discount: "新增折扣版本",
                fx: "新增汇率版本",
                import: "导入成本 CSV",
                credential: "更新 SP 凭据",
                "entity-edit": "编辑信息",
                status: edit?.active ? "停用对象" : "重新启用",
                help: "关于 AzureLens",
              } as Row
            )[modal]
          }
          close={() => setModal("")}
        >
          {modal === "help" ? (
            <div className="modal-content">
              <p>AzureLens 用于 Azure CSP 客户的内部成本与估算 ACR 管理。</p>
              <p>
                内部估算 ACR = 经验证的折扣前成本 × 消费日客户折扣 × 当日有效
                USD 汇率。
              </p>
              <div className="notice">
                支持 CSV、Azure API 和 Blob Export；自动采集状态请查看系统设置 →
                同步中心。
              </div>
            </div>
          ) : (
            <EntityForm
              key={modal + (edit?.id || "")}
              modal={modal}
              edit={edit}
              customers={customers}
              tenants={tenants}
              subs={subs}
              user={user}
              close={() => setModal("")}
              onSave={save}
              rangeStart={start}
              rangeEnd={end}
              onImport={async (data) => {
                const result = await api("/sync/imports", {
                  method: "POST",
                  body: data,
                });
                setToast(
                  result.duplicate
                    ? "相同文件已导入，本次未重复入账"
                    : "CSV 已校验并发布",
                );
                setModal("");
                refresh();
              }}
            />
          )}
        </Modal>
      )}
      {detail && (
        <Modal
          title={detail._type === "customer" ? "客户详情" : "导入批次详情"}
          close={() => setDetail(null)}
        >
          <div className="modal-content">
            {detail._type === "customer" ? (
              <>
                <h3>{detail.name}</h3>
                <p className="muted">{detail.notes || "暂无备注"}</p>
                <div className="detail-grid">
                  <div>
                    <small>租户</small>
                    <strong>{detail.tenants}</strong>
                  </div>
                  <div>
                    <small>订阅</small>
                    <strong>{detail.subscriptions}</strong>
                  </div>
                </div>
                <h4>历史折扣版本（UTC）</h4>
                {detail.discounts.map((d: Row) => (
                  <div className="detail-line" key={d.id}>
                    <span>{d.day} 起</span>
                    <strong>{Number(d.factor) * 100}%</strong>
                  </div>
                ))}
                <h4>关联租户</h4>
                {tenants
                  .filter((t) => t.customer_id === detail.id)
                  .map((t) => (
                    <div className="detail-line" key={t.id}>
                      {t.name}
                      <Badge tone={t.active ? "green" : "gray"}>
                        {t.active ? "启用" : "停用"}
                      </Badge>
                    </div>
                  ))}
                <button
                  className="primary mt"
                  onClick={() => {
                    setScope(detail.id);
                    setTenantScope("");
                    setSubScope("");
                    setDetail(null);
                    setPage("dashboard");
                  }}
                >
                  查看客户消费
                  <ArrowRight size={16} />
                </button>
              </>
            ) : (
              <>
                <h3>{detail.filename}</h3>
                <dl className="details">
                  <dt>批次 ID</dt>
                  <dd className="mono">{detail.id}</dd>
                  <dt>订阅</dt>
                  <dd>{subName(detail.subscription_id)}</dd>
                  <dt>覆盖日期</dt>
                  <dd>
                    {detail.start} 至 {detail.end}
                  </dd>
                  <dt>记录数</dt>
                  <dd>{detail.row_count}</dd>
                  <dt>价格基数</dt>
                  <dd>
                    {detail.verified
                      ? "管理员已声明验证"
                      : "未验证，不计入 ACR"}
                  </dd>
                  <dt>完整度</dt>
                  <dd>{detail.complete ? "管理员/操作者声明完整" : "未知"}</dd>
                  <dt>核实依据</dt>
                  <dd>{detail.evidence || "未提供"}</dd>
                  <dt>修正原因</dt>
                  <dd>{detail.reason || "首次导入"}</dd>
                  <dt>导入时间</dt>
                  <dd>{new Date(detail.created).toLocaleString("zh-CN")}</dd>
                </dl>
              </>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
function Stat({
  label,
  value,
  icon: Icon,
  primary = false,
}: {
  label: string;
  value: string;
  icon: any;
  primary?: boolean;
}) {
  return (
    <section className={"stat " + (primary ? "highlight" : "")}>
      <div className="stat-top">
        <span>{label}</span>
        <Icon size={18} />
      </div>
      <strong>{value}</strong>
    </section>
  );
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty">
      <span>
        <Database size={23} />
      </span>
      <strong>{title}</strong>
    </div>
  );
}
function BatchTable({
  batches,
  subName,
  onDetail,
}: {
  batches: Row[];
  subName: (id: string) => string;
  onDetail: (b: Row) => void;
}) {
  return batches.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>来源文件</th>
            <th>订阅 / 覆盖日期</th>
            <th>记录数</th>

            <th>导入时间</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {batches.map((b) => (
            <tr key={b.id}>
              <td>
                <span className="file-cell">
                  <span className="file-icon">
                    <FileSpreadsheet size={18} />
                  </span>
                  <span>
                    <strong>{b.filename}</strong>
                  </span>
                </span>
              </td>
              <td>
                {subName(b.subscription_id)}
                <small>
                  {b.start} — {b.end}
                </small>
              </td>
              <td>{b.row_count.toLocaleString()}</td>

              <td className="muted">
                {new Date(b.created).toLocaleString("zh-CN", {
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </td>
              <td>
                <button className="text-button" onClick={() => onDetail(b)}>
                  详情
                  <ChevronRight size={14} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty
      title="暂无导入记录"
      text="下载模板并导入第一份成本 CSV，开始构建你的数据视图。"
    />
  );
}
function EntityForm({
  modal,
  edit,
  customers,
  tenants,
  subs,
  user,
  close,
  onSave,
  onImport,
  rangeStart,
  rangeEnd,
}: {
  modal: string;
  edit: Row | null;
  customers: Row[];
  tenants: Row[];
  subs: Row[];
  user: Row;
  close: () => void;
  onSave: (path: string, body: Row, method?: string) => Promise<void>;
  onImport: (data: FormData) => Promise<void>;
  rangeStart: string;
  rangeEnd: string;
}) {
  const [tenantCount, setTenantCount] = useState(0);
  const [discountFactor, setDiscountFactor] = useState(
    String(edit?.factor ?? "1"),
  );
  const [discountDate, setDiscountDate] = useState(utcDay());
  const discountVersions =
    customers.find((c) => c.id === edit?.id)?.discounts ||
    edit?.discounts ||
    [];
  const discountImpact = previewDiscount(
    discountVersions,
    discountFactor,
    discountDate,
    rangeStart,
    rangeEnd,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [file, setFile] = useState<File | null>(null),
    [verified, setVerified] = useState(false),
    [complete, setComplete] = useState(false);
  const input = (
    name: string,
    label: string,
    props: React.InputHTMLAttributes<HTMLInputElement> = {},
    hint?: string,
  ) => (
    <Field label={label} hint={hint}>
      <input name={name} required {...props} />
    </Field>
  );
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        const data = new FormData(e.currentTarget);
        const body = Object.fromEntries(data) as Row;
        if (modal === "customer" && Number(body.tenant_count) > 0) {
          body.tenants = Array.from(
            { length: Number(body.tenant_count) },
            (_, i) => ({
              id: body["tenant_" + i],
              client_id: body["client_" + i],
              client_secret: body["secret_" + i],
            }),
          );
        }
        try {
          if (modal === "import") {
            if (!file) throw new Error("请选择 CSV 文件");
            data.set("file", file);
            data.set("verified", String(verified));
            data.set("complete", String(complete));
            await onImport(data);
          } else if (modal === "customer")
            await onSave(
              body.tenants ? "/customers/onboarding" : "/customers",
              body,
            );
          else if (modal === "customer-edit")
            await onSave(
              "/customers/" + edit!.id,
              { name: body.name, notes: body.notes },
              "PATCH",
            );
          else if (modal === "discount") {
            if (!discountImpact.valid)
              throw new Error(
                discountImpact.conflict
                  ? "该生效日期已有折扣版本，请选择其他日期。"
                  : "请填写有效的折扣系数和生效日期。",
              );
            await onSave("/customers/" + edit!.id + "/discounts", body);
          } else if (modal === "tenant") await onSave("/tenants", body);
          else if (modal === "subscription")
            await onSave("/subscriptions", body);
          else if (modal === "fx") await onSave("/settings/fx-rates", body);
          else if (modal === "credential")
            await onSave("/tenants/" + edit!.id + "/credentials", body, "PUT");
          else if (modal === "entity-edit")
            await onSave("/" + edit!.kind + "/" + edit!.id, body, "PATCH");
          else if (modal === "status")
            await onSave(
              "/" + edit!.kind + "/" + edit!.id,
              { active: !edit!.active },
              "PATCH",
            );
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="modal-content">
        {(modal === "customer" || modal === "customer-edit") && (
          <>
            {input("name", "客户名称", {
              defaultValue: edit?.name,
              placeholder: "例如：远山科技",
              maxLength: 150,
            })}
            <Field label="备注">
              <textarea
                name="notes"
                defaultValue={edit?.notes}
                placeholder="客户背景、合作说明（选填）"
                maxLength={2000}
              />
            </Field>
            {modal === "customer" && (
              <>
                <div className="form-row">
                  {input(
                    "factor",
                    "统一折扣系数",
                    {
                      defaultValue: "0.85",
                      type: "number",
                      min: 0,
                      max: 1,
                      step: "0.0001",
                    },
                    "85 折填写 0.85",
                  )}
                  {input("effective_date", "生效日期（UTC）", {
                    type: "date",
                    defaultValue: utcDay(),
                  })}
                </div>
                {user.role === "admin" && (
                  <>
                    <Field label="同时配置租户">
                      <select
                        name="tenant_count"
                        value={tenantCount}
                        onChange={(e) => setTenantCount(Number(e.target.value))}
                      >
                        <option value="0">稍后添加</option>
                        {[1, 2, 3].map((n) => (
                          <option value={n} key={n}>
                            {n} 组 Tenant / SP
                          </option>
                        ))}
                      </select>
                    </Field>
                    {Array.from({ length: tenantCount }, (_, i) => (
                      <div key={i}>
                        <h4>租户 {i + 1}</h4>
                        {input("tenant_" + i, "Tenant ID")}
                        {input("client_" + i, "Client ID")}
                        {input("secret_" + i, "Client Secret", {
                          type: "password",
                          autoComplete: "new-password",
                        })}
                      </div>
                    ))}
                  </>
                )}
                <div className="notice">
                  可选择一次配置 1–3 组租户及
                  SP，保存后后台自动认证、发现订阅并回填历史。
                </div>
              </>
            )}
          </>
        )}
        {modal === "discount" && (
          <>
            <div className="form-row">
              {input(
                "factor",
                "折扣系数",
                {
                  type: "number",
                  min: 0,
                  max: 1,
                  step: "0.0001",
                  value: discountFactor,
                  onChange: (e) => setDiscountFactor(e.target.value),
                },
                "1 = 不打折；0.85 = 按原价的 85% 核算",
              )}
              {input("effective_date", "生效日期（UTC）", {
                type: "date",
                value: discountDate,
                onChange: (e) => setDiscountDate(e.target.value),
              })}
            </div>
            <DiscountPolicyPreview
              versions={discountVersions}
              factor={discountFactor}
              effective={discountDate}
              start={rangeStart}
              end={rangeEnd}
            />
          </>
        )}
        {modal === "tenant" && (
          <>
            <Field label="所属客户">
              <select name="customer_id" required defaultValue="">
                <option value="" disabled>
                  选择客户
                </option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            {input("name", "租户名称", { placeholder: "例如：远山生产环境" })}
            {input("id", "Tenant ID", {
              placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            })}
            {user.role === "admin" && (
              <>
                <div className="form-divider">SP 配置（可稍后补充）</div>
                {input("client_id", "Client ID", {
                  required: false,
                  placeholder: "应用（客户端）ID",
                  autoComplete: "off",
                })}
                {input("client_secret", "Client Secret", {
                  required: false,
                  type: "password",
                  placeholder: "仅输入新密钥，不会回显",
                  autoComplete: "new-password",
                })}
              </>
            )}
          </>
        )}
        {modal === "subscription" && (
          <>
            <Field label="所属租户">
              <select name="tenant_id" required defaultValue="">
                <option value="" disabled>
                  选择租户
                </option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {customers.find((c) => c.id === t.customer_id)?.name} /{" "}
                    {t.name}
                  </option>
                ))}
              </select>
            </Field>
            {input("name", "订阅显示名称", {
              placeholder: "例如：Production Subscription",
            })}
            {input("id", "Subscription ID", {
              placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            })}
            <div className="notice">
              手工录入后状态为“待 Azure 验证”，不会模拟权限校验成功。
            </div>
          </>
        )}
        {modal === "credential" && (
          <>
            <div className="notice">
              替换 {edit?.name} 的 SP 配置。历史成本与订阅保持不变。
            </div>
            {input("client_id", "Client ID", {
              defaultValue: edit?.client_id,
              autoComplete: "off",
            })}
            {input("client_secret", "新 Client Secret", {
              type: "password",
              autoComplete: "new-password",
            })}
          </>
        )}
        {modal === "entity-edit" &&
          input("name", "显示名称", { defaultValue: edit?.name })}
        {modal === "status" && (
          <div className="notice">
            确认{edit?.active ? "停用" : "启用"}「{edit?.name}」？
            {edit?.active
              ? "停用后该范围不能导入新成本，历史数据保留。"
              : "启用后可以继续导入成本。"}
          </div>
        )}
        {modal === "fx" && (
          <>
            <div className="form-row">
              {input("currency", "原币 ISO 代码", {
                placeholder: "CNY",
                pattern: "[A-Z]{3}",
                maxLength: 3,
              })}
              {input("rate", "1 原币 = 多少 USD", {
                type: "number",
                min: "0.000000000001",
                step: "any",
                placeholder: "0.14",
              })}
            </div>
            {input("effective_date", "生效日期（UTC）", {
              type: "date",
              defaultValue: utcDay(),
            })}
            <div className="notice">
              相同货币、相同生效日期不能重复。新增版本会按日期重新计算相关成本。
            </div>
          </>
        )}
        {modal === "import" && (
          <>
            <Field label="目标订阅">
              <select name="subscription_id" required defaultValue="">
                <option value="" disabled>
                  选择订阅
                </option>
                {subs
                  .filter((s) => s.active)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
              </select>
            </Field>
            <div className="form-row">
              {input("start", "覆盖开始（UTC）", {
                type: "date",
                defaultValue: yesterday(),
              })}
              {input("end", "覆盖结束（UTC）", {
                type: "date",
                defaultValue: yesterday(),
              })}
            </div>
            <Field label="标准 CSV 文件">
              <div className="file-upload">
                <FileSpreadsheet size={28} />
                <strong>{file ? file.name : "选择 CSV 文件"}</strong>
                <small>UTF-8 · 最大 10 MB · 最多 50,000 行</small>
                <input
                  aria-label="CSV 文件"
                  type="file"
                  accept=".csv,text/csv"
                  required
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                />
              </div>
            </Field>
            <div className="notice">
              每个有记录的订阅日将整体发布为一个版本（包含所有服务）。修正文件必须包含该日全部服务，不能只上传差额。
            </div>
            {user.role === "admin" && (
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={verified}
                  onChange={(e) => setVerified(e.target.checked)}
                />
                <span>
                  已核实原始成本为客户折扣前基数
                  <small>未勾选的成本将保留，但不参与 ACR 计算</small>
                </span>
              </label>
            )}
            <label className="check-field">
              <input
                type="checkbox"
                checked={complete}
                onChange={(e) => setComplete(e.target.checked)}
              />
              <span>
                声明覆盖日期内全部服务数据完整
                <small>每一天都须有记录；零消费需显式填写 0</small>
              </span>
            </label>
            <Field label="核实依据 / 完整度证据">
              <textarea
                name="evidence"
                required={verified || complete}
                maxLength={2000}
                placeholder="说明成本价格口径、对账依据和完整覆盖证明"
              />
            </Field>
            <Field label="修正原因（覆盖已有数据时必填）">
              <input
                name="reason"
                maxLength={2000}
                placeholder="例如：补入迟到费用，替换原批次"
              />
            </Field>
          </>
        )}
        {error && (
          <div className="error" role="alert">
            <AlertCircle size={17} />
            {error}
          </div>
        )}
      </div>
      <footer className="modal-footer">
        <button type="button" className="secondary" onClick={close}>
          取消
        </button>
        <button
          type="submit"
          className="primary"
          disabled={busy || (modal === "discount" && !discountImpact.valid)}
        >
          {busy ? (
            <LoaderCircle size={16} className="spin" />
          ) : modal === "import" ? (
            <FileSpreadsheet size={16} />
          ) : (
            <Check size={16} />
          )}
          {busy
            ? "处理中…"
            : modal === "import"
              ? "校验并导入"
              : modal === "discount"
                ? "保存折扣版本"
                : "确认保存"}
        </button>
      </footer>
    </form>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
