import { useEffect, useState } from "react";
import { ArrowRight, Layers, Search } from "lucide-react";
import {
  costApi,
  costCurrency,
  costStates,
  rawCosts,
  type CostRow,
} from "./costUi";
export default function SubscriptionConsumption({
  start,
  end,
  customerId,
  tenantId,
  subscriptionId,
  onResource,
}: {
  start: string;
  end: string;
  customerId: string;
  tenantId: string;
  subscriptionId: string;
  onResource: (row: CostRow) => void;
}) {
  const [data, setData] = useState<CostRow | null>(null),
    [error, setError] = useState(""),
    [page, setPage] = useState(1),
    [search, setSearch] = useState(""),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setRevision((r) => r + 1), 15000);
    return () => clearInterval(t);
  }, []);
  useEffect(
    () => setPage(1),
    [start, end, customerId, tenantId, subscriptionId, search],
  );
  useEffect(() => {
    let active = true;
    const params = new URLSearchParams({
      start,
      end,
      customer_id: customerId,
      tenant_id: tenantId,
      subscription_id: subscriptionId,
      page: String(page),
      search,
    });
    const timer = setTimeout(
      () =>
        costApi("/subscriptions/consumption?" + params)
          .then((d) => {
            if (active) {
              setData(d);
              setError("");
            }
          })
          .catch((e) => {
            if (active) setError(e.message);
          }),
      150,
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
    revision,
  ]);
  return (
    <section
      className="panel subscription-consumption"
      aria-label="订阅消费统计"
    >
      <div className="panel-heading">
        <div>
          <h2>订阅消费统计</h2>
        </div>
        <Layers size={19} className="muted" />
      </div>
      <div className="table-toolbar">
        <div className="search">
          <Search size={16} />
          <input
            aria-label="搜索订阅消费"
            placeholder="搜索订阅、客户或租户…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="muted">{data?.total ?? "—"} 个订阅归属记录</span>
      </div>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>客户 / 租户</th>
              <th>订阅</th>
              <th>原始费用（原币）</th>
              <th>ACR · USD</th>
              <th>已核验 ACR · USD</th>
              <th>已采集订阅日</th>
              <th>同步 / 最近成功时间</th>
              <th>资源明细</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items || []).map((row: CostRow) => (
              <tr key={row.subscription_id + row.customer_id + row.tenant_id}>
                <td>
                  <strong>{row.customer_name}</strong>
                  <small>{row.tenant_name}</small>
                </td>
                <td>
                  <strong>{row.subscription_name}</strong>
                  <small className="mono">{row.subscription_id}</small>
                </td>
                <td className="cost-value">{rawCosts(row.raw_costs)}</td>
                <td>
                  <strong>{costCurrency(row.trial_usd)}</strong>
                </td>
                <td>{costCurrency(row.verified_usd)}</td>
                <td>
                  {row.received_days} / {row.expected_days}
                  <small>{row.records} 条有效服务日费用</small>
                </td>
                <td>
                  <span
                    className={
                      "badge " +
                      (row.sync_state === "SUCCEEDED" ? "green" : "gray")
                    }
                  >
                    {costStates[row.sync_state] || row.sync_state}
                  </span>
                  <small>
                    {row.last_sync
                      ? new Date(row.last_sync).toLocaleString("zh-CN")
                      : "尚未成功同步"}
                  </small>
                </td>
                <td>
                  <button
                    className="text-button"
                    onClick={() => onResource(row)}
                  >
                    查看资源 <ArrowRight size={13} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && !data.items.length && (
        <div className="resource-empty">暂无匹配的订阅</div>
      )}
      {!data && !error && (
        <div className="resource-empty">正在加载订阅消费…</div>
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
  );
}
