import { useEffect, useState } from "react";
import {
  Box,
  RefreshCw,
  Search,
  ArrowRight,
  Database,
  AlertCircle,
  X,
  Layers,
} from "lucide-react";
import {
  costApi,
  costCurrency,
  costStates,
  rawCosts,
  type CostRow,
} from "./costUi";
const errorLabels: Record<string, string> = {
  AZURE_HTTP_429: "Azure 请求限流，后台将按等待时间重试",
  RESOURCE_DIMENSION_UNAVAILABLE: "当前 Azure 计费 Scope 不支持资源维度",
  COST_ACCESS_DENIED: "当前 SP 无资源成本读取权限",
  SP_AUTH_FAILED: "SP 身份认证失败",
  RESOURCE_COST_ROW_LIMIT: "该分区资源记录过多，请缩小采集区间",
  UNSUPPORTED_RESOURCE_COST_SCHEMA: "源资源费用格式暂不支持",
};
export default function ResourceDetails({
  customers,
  tenants,
  subscriptions,
  customerId,
  tenantId,
  subscriptionId,
  start,
  end,
  onCustomer,
  onTenant,
  onSubscription,
  onStart,
  onEnd,
}: {
  customers: CostRow[];
  tenants: CostRow[];
  subscriptions: CostRow[];
  customerId: string;
  tenantId: string;
  subscriptionId: string;
  start: string;
  end: string;
  onCustomer: (id: string) => void;
  onTenant: (id: string) => void;
  onSubscription: (id: string) => void;
  onStart: (day: string) => void;
  onEnd: (day: string) => void;
}) {
  const [data, setData] = useState<CostRow | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [search, setSearch] = useState(""),
    [service, setService] = useState(""),
    [page, setPage] = useState(1),
    [rev, setRev] = useState(0),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<CostRow | null>(null),
    [detail, setDetail] = useState<CostRow | null>(null),
    [detailPage, setDetailPage] = useState(1);
  const available = subscriptions.filter(
    (s) =>
      s.id === subscriptionId ||
      ((!customerId || s.customer_id === customerId) &&
        (!tenantId || s.tenant_id === tenantId)),
  );
  useEffect(() => {
    if (!available.some((s) => s.id === subscriptionId) && available.length)
      onSubscription(available[0].id);
  }, [customerId, tenantId, subscriptionId, subscriptions]);
  useEffect(() => {
    setService("");
    setSearch("");
  }, [subscriptionId]);
  useEffect(() => {
    const t = setInterval(() => setRev((r) => r + 1), 15000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    setPage(1);
    setSelected(null);
    setDetail(null);
    setData(null);
  }, [start, end, customerId, tenantId, subscriptionId, service]);
  useEffect(() => setPage(1), [search]);
  useEffect(() => {
    if (!subscriptionId || !start || !end) {
      setData(null);
      return;
    }
    let active = true;
    const p = new URLSearchParams({
      start,
      end,
      subscription_id: subscriptionId,
      customer_id: customerId,
      tenant_id: tenantId,
      page: String(page),
      search,
      service,
    });
    const timer = setTimeout(
      () =>
        costApi("/resource-costs?" + p)
          .then((d) => {
            if (active) {
              setData(d);
              setError("");
            }
          })
          .catch((e) => {
            if (active) setError(e.message);
          }),
      180,
    );
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [
    start,
    end,
    customerId,
    tenantId,
    subscriptionId,
    page,
    search,
    service,
    rev,
  ]);
  useEffect(() => {
    if (!selected || !start || !end) return;
    let active = true;
    const p = new URLSearchParams({
      start,
      end,
      subscription_id: subscriptionId,
      customer_id: selected.customer_id,
      tenant_id: selected.tenant_id,
      service,
      page: String(detailPage),
    });
    costApi("/resource-costs/" + selected.resource_key + "/details?" + p)
      .then((d) => {
        if (active) setDetail(d);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [
    selected,
    start,
    end,
    customerId,
    tenantId,
    subscriptionId,
    detailPage,
    service,
    rev,
  ]);
  const sync = async () => {
    setBusy(true);
    try {
      const result = await costApi(
        "/subscriptions/" + subscriptionId + "/resource-costs/sync",
        { method: "POST", body: JSON.stringify({ start, end }) },
      );
      setNotice("资源费用采集已排队 · 任务 " + result.job_id.slice(0, 8));
      setRev((r) => r + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const state = data?.state;
  return (
    <>
      <div className="resource-filter-bar">
        <label>
          客户
          <select
            aria-label="资源客户"
            value={customerId}
            onChange={(e) => onCustomer(e.target.value)}
          >
            <option value="">全部客户</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          租户
          <select
            aria-label="资源租户"
            value={tenantId}
            onChange={(e) => onTenant(e.target.value)}
          >
            <option value="">全部租户</option>
            {tenants
              .filter((t) => !customerId || t.customer_id === customerId)
              .map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
          </select>
        </label>
        <label className="resource-sub-filter">
          订阅
          <select
            aria-label="资源订阅"
            value={subscriptionId}
            onChange={(e) => onSubscription(e.target.value)}
          >
            <option value="">选择订阅</option>
            {available.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          起始日期（UTC）
          <input
            type="date"
            aria-label="资源起始日期"
            value={start}
            onChange={(e) => onStart(e.target.value)}
          />
        </label>
        <label>
          结束日期（UTC）
          <input
            type="date"
            aria-label="资源结束日期"
            value={end}
            onChange={(e) => onEnd(e.target.value)}
          />
        </label>
      </div>

      {error && (
        <div className="error" role="alert">
          <AlertCircle size={17} />
          {error}
        </div>
      )}
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      <section className="panel resource-summary">
        <div className="panel-heading">
          <div>
            <h2>订阅资源费用</h2>
          </div>
          <div className="row-actions">
            <button className="secondary" onClick={() => setRev((r) => r + 1)}>
              <RefreshCw size={15} />
              刷新
            </button>
            <button
              className="primary"
              disabled={
                busy || !subscriptionId || !start || !end || end < start
              }
              onClick={sync}
            >
              <RefreshCw size={15} />
              {busy ? "正在排队…" : "采集所选区间"}
            </button>
          </div>
        </div>
        <div className="resource-metrics">
          <div>
            <small>费用关联资源 / 归属记录</small>
            <strong>{data?.total ?? "—"}</strong>
          </div>
          <div>
            <small>当前筛选原始费用（各币种）</small>
            <strong className="resource-total-money">
              {data ? rawCosts(data.totals) : "—"}
            </strong>
          </div>
          <div>
            <small>最近资源采集</small>
            <strong className="resource-state-name">
              {costStates[state?.state] || state?.state || "尚未采集"}
            </strong>
            <small>
              {state?.last_sync
                ? new Date(state.last_sync).toLocaleString("zh-CN")
                : "等待首次采集"}
            </small>
          </div>
        </div>
        {state?.error && (
          <div className="quality-banner">
            <AlertCircle size={17} />
            <span>
              {errorLabels[state.error] || state.error}
              {state.available &&
                " · 下次尝试 " +
                  new Date(state.available).toLocaleString("zh-CN")}
            </span>
          </div>
        )}
      </section>
      <section className="panel">
        <div className="table-toolbar">
          <div className="search">
            <Search size={17} />
            <input
              aria-label="搜索资源"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索资源名称、ID、资源组或类型…"
            />
          </div>
          <select
            aria-label="资源服务筛选"
            value={service}
            onChange={(e) => setService(e.target.value)}
          >
            <option value="">全部 Azure 服务</option>
            {(data?.services || []).map((s: string) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>资源 / Resource ID</th>
                <th>资源组 / 类型</th>
                <th>Azure 服务</th>
                <th>原始费用（原币）</th>
                <th>ACR · USD</th>
                <th>已核验 ACR · USD</th>
                <th>费用日期</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items || []).map((r: CostRow) => (
                <tr key={r.resource_key + r.customer_id + r.tenant_id}>
                  <td>
                    <strong>{r.name}</strong>
                    <small className="resource-id" title={r.resource_id}>
                      {r.resource_id || "源费用未关联 Resource ID"}
                    </small>
                  </td>
                  <td>
                    {r.resource_group}
                    <small>{r.resource_type}</small>
                  </td>
                  <td>
                    {r.services.slice(0, 3).join("、")}
                    {r.services.length > 3 && (
                      <small>共 {r.services.length} 个服务</small>
                    )}
                  </td>
                  <td className="cost-value">{rawCosts(r.raw_costs)}</td>
                  <td>
                    <strong>{costCurrency(r.trial_usd)}</strong>
                  </td>
                  <td>{costCurrency(r.verified_usd)}</td>
                  <td>
                    {r.first_day}
                    <small>至 {r.last_day}</small>
                  </td>
                  <td>
                    <button
                      className="text-button"
                      onClick={() => {
                        setSelected(r);
                        setDetailPage(1);
                        setDetail(null);
                      }}
                    >
                      每日明细 <ArrowRight size={13} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {(!data || !data.items.length) && (
          <div className="resource-empty">
            <Box size={30} />
            <strong>
              {!subscriptionId
                ? "请选择订阅"
                : data
                  ? "当前区间暂无已采集资源费用"
                  : "正在读取资源费用…"}
            </strong>
          </div>
        )}
        <div className="panel-footer">
          <div className="row-actions">
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              上一页
            </button>
            <span>
              {page} / {Math.max(1, Math.ceil((data?.total || 0) / 25))}
            </span>
            <button
              disabled={page >= Math.ceil((data?.total || 0) / 25)}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </button>
          </div>
        </div>
      </section>
      {selected && (
        <section className="panel resource-daily" aria-label="资源每日费用明细">
          <div className="panel-heading">
            <div>
              <h2>{selected.name} · 每日费用明细</h2>
              <p className="resource-detail-id">
                {selected.resource_id || "未分配到资源的费用"}
              </p>
            </div>
            <button
              className="icon-button"
              aria-label="关闭资源明细"
              onClick={() => setSelected(null)}
            >
              <X size={18} />
            </button>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>UTC 消费日</th>
                  <th>Azure 服务</th>
                  <th>原始费用</th>
                  <th>折扣 / FX</th>
                  <th>ACR · USD</th>
                  <th>已核验 ACR · USD</th>
                  <th>来源版本时间</th>
                </tr>
              </thead>
              <tbody>
                {(detail?.items || []).map((r: CostRow, i: number) => (
                  <tr key={r.day + r.service + r.currency + i}>
                    <td>{r.day}</td>
                    <td>{r.service}</td>
                    <td>
                      {costCurrency(r.raw, r.currency)} {r.currency}
                    </td>
                    <td>
                      {r.factor ?? "缺少折扣"} / {r.rate ?? "缺少汇率"}
                    </td>
                    <td>{costCurrency(r.trial_usd)}</td>
                    <td>{costCurrency(r.verified_usd)}</td>
                    <td>{new Date(r.created).toLocaleString("zh-CN")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="panel-footer">
            <div className="row-actions">
              <button
                disabled={detailPage <= 1}
                onClick={() => setDetailPage((p) => p - 1)}
              >
                上一页
              </button>
              <span>
                {detailPage} /{" "}
                {Math.max(1, Math.ceil((detail?.total || 0) / 25))}
              </span>
              <button
                disabled={detailPage >= Math.ceil((detail?.total || 0) / 25)}
                onClick={() => setDetailPage((p) => p + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        </section>
      )}
    </>
  );
}
