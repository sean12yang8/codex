import { useEffect, useState } from "react";
import {
  RefreshCw,
  Play,
  ShieldCheck,
  Download,
  Plus,
  X,
  AlertCircle,
  Check,
} from "lucide-react";
type R = Record<string, any>;
async function call(path: string, method = "GET", body?: R) {
  const r = await fetch("/api/v1" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await r.json();
  if (r.status === 401) window.dispatchEvent(new Event("acr:unauthorized"));
  if (!r.ok) throw new Error(d.detail?.message || "操作失败");
  return d;
}
const status: R = {
  PENDING: "等待处理",
  NOT_SEEN: "本次未发现",
  QUEUED: "排队中",
  RUNNING: "执行中",
  RETRY_WAIT: "等待重试",
  SUCCEEDED: "成功",
  FAILED: "失败",
  CANCELLED: "已取消",
  PARTIAL_SUCCEEDED: "部分成功",
  NO_DATA: "无数据",
  OPEN: "待处理",
  ACKNOWLEDGED: "已确认",
  MUTED: "已静音",
  RESOLVED: "已恢复",
  CLOSED: "已关闭",
};
function Tag({ value }: { value: string }) {
  return (
    <span
      className={
        "badge " +
        (value === "SUCCEEDED" || value === "RESOLVED"
          ? "green"
          : value === "FAILED"
            ? "amber"
            : "blue")
      }
    >
      {status[value] || value || "未执行"}
    </span>
  );
}
type F = {
  key: string;
  label: string;
  type?: string;
  options?: { value: string; label: string }[];
  value?: any;
  required?: boolean;
  hint?: string;
};
export default function Operations({
  mode,
  user,
  initialTenant = "",
}: {
  mode: string;
  user: R;
  initialTenant?: string;
}) {
  const [data, setData] = useState<R>({}),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [rev, setRev] = useState(0),
    [form, setForm] = useState<{
      title: string;
      fields: F[];
      submit: (d: R) => Promise<any>;
    } | null>(null),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState(initialTenant),
    [detail, setDetail] = useState<R | null>(null);
  const load = async () => {
    try {
      const [c, t, s, j, a, e, w] = await Promise.all(
        [
          "/customers",
          "/tenants",
          "/subscriptions",
          "/sync/jobs",
          "/alerts",
          "/reports/tasks",
          "/settings/worker",
        ].map((p) => call(p)),
      );
      setData({
        customers: c,
        tenants: t,
        subs: s,
        jobs: j,
        alerts: a,
        exports: e,
        worker: w,
      });
      if (t.length) setSelected((old) => old || t[0].id);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [rev, mode]);
  const action = async (path: string, method = "POST", body?: R) => {
    try {
      await call(path, method, body);
      setNotice("操作已提交");
      setRev((x) => x + 1);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const open = (title: string, fields: F[], submit: (d: R) => Promise<any>) =>
    setForm({ title, fields, submit });
  const tenants = (data.tenants || []) as R[],
    customers = (data.customers || []) as R[],
    subs = (data.subs || []) as R[];
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const dateFields: F[] = [
    { key: "start", label: "开始日期（UTC）", type: "date", value: yesterday },
    { key: "end", label: "结束日期（UTC）", type: "date", value: yesterday },
  ];
  const jobForm = () =>
    open(
      "创建同步任务",
      [
        {
          key: "scope_type",
          label: "范围类型",
          options: [
            { value: "tenant", label: "租户" },
            { value: "customer", label: "客户" },
            { value: "subscription", label: "订阅" },
          ],
        },
        {
          key: "scope_id",
          label: "对象",
          options: [
            ...tenants.map((t) => ({ value: t.id, label: "租户 · " + t.name })),
            ...customers.map((t) => ({
              value: t.id,
              label: "客户 · " + t.name,
            })),
            ...subs.map((t) => ({ value: t.id, label: "订阅 · " + t.name })),
          ],
        },
        ...dateFields,
        {
          key: "kind",
          label: "任务类型",
          options: [
            { value: "sync", label: "手动同步" },
            { value: "backfill", label: "历史回填（低优先级）" },
          ],
        },
      ],
      (d) => call("/sync/jobs", "POST", d),
    );
  const named = (id: string) =>
    tenants.find((t) => t.id === id)?.name ||
    subs.find((t) => t.id === id)?.name ||
    customers.find((t) => t.id === id)?.name ||
    id;
  return (
    <>
      <div className="info-strip">
        <span>
          <ShieldCheck size={16} />
          后台工作进程：{data.worker?.ready ? "运行中" : "尚未启动或心跳过期"}
        </span>
        <button className="secondary" onClick={load}>
          <RefreshCw size={14} />
          刷新
        </button>
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="notice" onClick={() => setNotice("")}>
          <Check size={16} />
          {notice}
        </div>
      )}
      {mode === "jobs" && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>同步任务中心</h2>
            </div>
            <button className="primary" onClick={jobForm}>
              <Plus size={16} />
              创建任务
            </button>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>对象 / Job ID</th>
                  <th>类型 / UTC 范围</th>
                  <th>状态 / 进度</th>
                  <th>重试 / 错误</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {(data.jobs || []).map((j: R) => (
                  <tr key={j.id}>
                    <td>
                      {named(j.scope_id)}
                      <small className="mono">{j.id.slice(0, 8)}</small>
                    </td>
                    <td>
                      {(
                        {
                          sync: "手动同步",
                          verify: "接入校验",
                          daily: "每日同步",
                          refresh: "补充刷新",
                          backfill: "历史回填",
                          resources: "资源费用采集",
                          tier: "层级读取",
                        } as R
                      )[j.kind] || j.kind}
                      <small>
                        {j.start} — {j.end}
                      </small>
                    </td>
                    <td>
                      <Tag value={j.state} />
                      <small>{j.progress || "进度未知"}</small>
                    </td>
                    <td>
                      {j.attempts}
                      <small>{j.error}</small>
                      {j.error === "AZURE_HTTP_429" && (
                        <small>Azure 请求限流，等待配额恢复</small>
                      )}
                      {j.state === "RETRY_WAIT" && (
                        <small>
                          下次尝试：
                          {new Date(j.available).toLocaleString("zh-CN")}
                        </small>
                      )}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button onClick={() => setDetail(j)}>详情</button>
                        {["QUEUED", "RUNNING", "RETRY_WAIT"].includes(
                          j.state,
                        ) ? (
                          <button
                            onClick={() =>
                              action("/sync/jobs/" + j.id + "/cancel")
                            }
                          >
                            取消
                          </button>
                        ) : (
                          ["FAILED", "CANCELLED", "PARTIAL_SUCCEEDED"].includes(
                            j.state,
                          ) && (
                            <button
                              onClick={() =>
                                action("/sync/jobs/" + j.id + "/retry")
                              }
                            >
                              重试
                            </button>
                          )
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.jobs?.length && <div className="empty">暂无同步任务</div>}
          </div>
        </section>
      )}
      {mode === "connections" && (
        <>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>Azure 接入与四阶段校验</h2>
              </div>
              <select
                aria-label="选择接入租户"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </div>
            <Connection
              selected={selected}
              refresh={rev}
              onAction={() => action("/tenants/" + selected + "/verify")}
            />
          </section>
          {selected && (
            <div className="title-actions mt">
              <button
                className="primary"
                onClick={() => action("/tenants/" + selected + "/verify")}
              >
                <Play size={15} />
                验证并发现订阅
              </button>
              <button className="secondary" onClick={jobForm}>
                按日期同步
              </button>
              {user.role === "admin" && (
                <>
                  <button
                    className="secondary"
                    onClick={async () => {
                      const c = await call(
                        "/tenants/" + selected + "/cost-basis",
                      );
                      open(
                        "成本基数验证声明",
                        [
                          {
                            key: "verified",
                            label: "已核实原始成本为客户折扣前基数",
                            type: "checkbox",
                            value: c.verified,
                          },
                          {
                            key: "evidence",
                            label: "对账与价格语义核实依据",
                            type: "textarea",
                            value: c.evidence,
                          },
                        ],
                        (d) =>
                          call(
                            "/tenants/" + selected + "/cost-basis",
                            "PUT",
                            d,
                          ),
                      );
                    }}
                  >
                    成本口径
                  </button>
                  <button
                    className="secondary"
                    onClick={async () => {
                      const c = await call("/tenants/" + selected + "/blob");
                      open(
                        "Blob Export 只读连接",
                        [
                          {
                            key: "account",
                            label: "Storage Account",
                            value: c.account,
                          },
                          {
                            key: "container",
                            label: "Container",
                            value: c.container,
                          },
                          {
                            key: "prefix",
                            label: "Export 路径前缀",
                            value: c.prefix,
                            required: false,
                            hint: "使用该租户 SP 的 Storage Blob Data Reader 权限",
                          },
                        ],
                        (d) => call("/tenants/" + selected + "/blob", "PUT", d),
                      );
                    }}
                  >
                    配置 Blob
                  </button>
                </>
              )}
            </div>
          )}
        </>
      )}
      {mode === "alerts" && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>告警中心</h2>
            </div>
            <button
              className="secondary"
              onClick={() =>
                open(
                  "告警阈值",
                  [
                    {
                      key: "customer_id",
                      label: "适用客户",
                      options: [
                        { value: "", label: "平台默认" },
                        ...customers.map((c) => ({
                          value: c.id,
                          label: c.name,
                        })),
                      ],
                    },
                    {
                      key: "percent",
                      label: "波动比例（%）",
                      value: "50",
                      type: "number",
                    },
                    {
                      key: "amount",
                      label: "绝对差额（USD）",
                      value: "100",
                      type: "number",
                    },
                    {
                      key: "zero_days",
                      label: "连续零消费天数",
                      value: 7,
                      type: "number",
                    },
                  ],
                  (d) =>
                    call("/settings/alert-rules", "PUT", {
                      ...d,
                      zero_days: Number(d.zero_days),
                    }),
                )
              }
            >
              规则设置
            </button>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>客户 / 类型</th>
                  <th>状态</th>
                  <th>时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {(data.alerts || []).map((a: R) => (
                  <tr key={a.id}>
                    <td>
                      {named(a.customer_id)}
                      <small>
                        {{
                          SYNC: "同步连续失败",
                          SPIKE: "消费波动",
                          ZERO: "连续零消费",
                        }[a.rule as string] || a.rule}
                      </small>
                    </td>
                    <td>
                      <Tag value={a.state} />
                      {!a.read && <small>未读</small>}
                    </td>
                    <td>{new Date(a.updated).toLocaleString("zh-CN")}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          onClick={() => {
                            setDetail(a);
                            action("/alerts/" + a.id + "/actions", "POST", {
                              action: "read",
                            });
                          }}
                        >
                          详情
                        </button>
                        {["ack", "mute", "close", "reopen"].map((v, i) => (
                          <button
                            key={v}
                            onClick={() =>
                              action("/alerts/" + a.id + "/actions", "POST", {
                                action: v,
                                mute_hours: 24,
                              })
                            }
                          >
                            {["确认", "静音24小时", "关闭", "重新打开"][i]}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.alerts?.length && <div className="empty">暂无告警</div>}
          </div>
        </section>
      )}
      {mode === "reports" && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>报表导出</h2>
            </div>
            <button
              className="primary"
              onClick={() =>
                open(
                  "生成报表",
                  [
                    ...dateFields,
                    {
                      key: "dimension",
                      label: "汇总维度",
                      options: [
                        "customer",
                        "tenant",
                        "subscription",
                        "service",
                      ].map((v, i) => ({
                        value: v,
                        label: ["客户", "租户", "订阅", "Azure 服务"][i],
                      })),
                    },
                    {
                      key: "granularity",
                      label: "时间粒度",
                      options: [
                        { value: "day", label: "按日" },
                        { value: "month", label: "按月" },
                      ],
                    },
                    {
                      key: "format",
                      label: "文件格式",
                      options: [
                        { value: "xlsx", label: "Excel" },
                        { value: "csv", label: "CSV" },
                      ],
                    },
                    {
                      key: "customer_id",
                      label: "客户范围",
                      options: [
                        { value: "", label: "全部客户" },
                        ...customers.map((c) => ({
                          value: c.id,
                          label: c.name,
                        })),
                      ],
                    },
                  ],
                  (d) => call("/reports/tasks", "POST", d),
                )
              }
            >
              <Download size={16} />
              新建导出
            </button>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>任务</th>
                  <th>创建时间</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {(data.exports || []).map((t: R) => (
                  <tr key={t.id}>
                    <td className="mono">{t.id.slice(0, 8)}</td>
                    <td>{new Date(t.created).toLocaleString("zh-CN")}</td>
                    <td>
                      <Tag value={t.state} />
                      <small>{t.error}</small>
                    </td>
                    <td>
                      {t.state === "SUCCEEDED" && (
                        <a
                          className="text-button"
                          href={"/api/v1/reports/tasks/" + t.id + "/download"}
                        >
                          安全下载
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {mode === "governance" && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>订阅归属与永久删除</h2>
            </div>
          </div>
          <div className="ops-actions">
            <button
              className="secondary"
              onClick={() =>
                open(
                  "转移订阅",
                  [
                    {
                      key: "id",
                      label: "订阅",
                      options: subs.map((s) => ({
                        value: s.id,
                        label: s.name,
                      })),
                    },
                    {
                      key: "tenant_id",
                      label: "目标租户",
                      options: tenants.map((t) => ({
                        value: t.id,
                        label: t.name,
                      })),
                    },
                    {
                      key: "effective_date",
                      label: "归属生效日期（UTC）",
                      type: "date",
                      value: yesterday,
                    },
                  ],
                  (d) =>
                    call("/subscriptions/" + d.id + "/transfer", "POST", d),
                )
              }
            >
              转移订阅
            </button>
            {user.role === "admin" && (
              <button
                className="secondary"
                onClick={() =>
                  open(
                    "永久删除影响预览",
                    [
                      {
                        key: "kind",
                        label: "对象类型",
                        options: [
                          { value: "customers", label: "客户" },
                          { value: "tenants", label: "租户" },
                          { value: "subscriptions", label: "订阅" },
                        ],
                      },
                      {
                        key: "id",
                        label: "目标对象",
                        options: [
                          ...customers.map((c) => ({
                            value: c.id,
                            label: "客户 · " + c.name,
                          })),
                          ...tenants.map((t) => ({
                            value: t.id,
                            label: "租户 · " + t.name,
                          })),
                          ...subs.map((s) => ({
                            value: s.id,
                            label: "订阅 · " + s.name,
                          })),
                        ],
                      },
                    ],
                    async (d) => {
                      const p = await call(
                        "/" + d.kind + "/" + d.id + "/purge-preview",
                        "POST",
                      );
                      setTimeout(
                        () =>
                          open(
                            "不可逆删除 · 确认影响范围",
                            [
                              {
                                key: "confirm",
                                label: "输入目标 ID 确认",
                                hint:
                                  d.id +
                                  "；影响 " +
                                  p.tenants +
                                  " 个租户、" +
                                  p.subscriptions +
                                  " 个订阅、" +
                                  p.facts +
                                  " 条成本记录。所有历史导出文件将失效。",
                              },
                            ],
                            (v) =>
                              call(
                                "/" + d.kind + "/" + d.id + "/purge",
                                "POST",
                                { ...v, challenge: p.challenge },
                              ),
                          ),
                        0,
                      );
                      return p;
                    },
                  )
                }
              >
                永久删除预览
              </button>
            )}
          </div>
          <div className="notice">
            永久删除默认关闭，须由运维完成备份与恢复演练后显式启用。已转移订阅按消费日期定向删除历史归属数据，保留其他客户的消费。
          </div>
        </section>
      )}
      {mode === "settings" && (
        <>
          <section className="panel mt">
            <div className="panel-heading">
              <div>
                <h2>平台管理</h2>
              </div>
            </div>
            <div className="ops-actions">
              <button
                className="secondary"
                onClick={async () => {
                  const c = await call("/settings/schedule");
                  open(
                    "每日 UTC 调度",
                    [
                      {
                        key: "enabled",
                        label: "启用每日自动同步",
                        type: "checkbox",
                        value: c.enabled,
                      },
                      {
                        key: "utc_time",
                        label: "UTC 调度时间",
                        type: "time",
                        value: c.utc_time,
                      },
                      {
                        key: "refresh_hours",
                        label: "补充同步间隔（小时，1–6）",
                        type: "number",
                        value: c.refresh_hours || 6,
                        hint: "上游数据迟到时补充检查；每日计划任务仍只创建一次。",
                      },
                    ],
                    (d) => call("/settings/schedule", "PUT", d),
                  );
                }}
              >
                自动同步计划
              </button>
              <button
                className="secondary"
                onClick={() =>
                  open(
                    "创建平台用户",
                    [
                      { key: "email", label: "邮箱", type: "email" },
                      { key: "name", label: "显示名称" },
                      {
                        key: "role",
                        label: "权限",
                        options: [
                          { value: "member", label: "成员" },
                          { value: "admin", label: "管理员" },
                        ],
                      },
                      {
                        key: "password",
                        label: "初始密码（至少12位）",
                        type: "password",
                      },
                    ],
                    (d) => call("/users", "POST", d),
                  )
                }
              >
                创建用户
              </button>
              <button
                className="secondary"
                onClick={async () => setDetail({ users: await call("/users") })}
              >
                管理用户
              </button>
              <button
                className="secondary"
                onClick={async () => {
                  const c = await call("/settings/sso");
                  open(
                    "Entra SSO 应用配置",
                    [
                      {
                        key: "enabled",
                        label: "启用 SSO",
                        type: "checkbox",
                        value: c.enabled,
                      },
                      {
                        key: "tenant_id",
                        label: "Entra Tenant ID",
                        value: c.tenant_id,
                      },
                      {
                        key: "client_id",
                        label: "应用 Client ID",
                        value: c.client_id,
                      },
                      {
                        key: "client_secret",
                        label: "新 Client Secret（留空保留）",
                        type: "password",
                        required: !c.secret_configured,
                        hint: "回调地址为当前 HTTPS 域名 + /api/v1/auth/sso/callback",
                      },
                    ],
                    (d) => call("/settings/sso", "PUT", d),
                  );
                }}
              >
                Entra SSO
              </button>
              <button
                className="secondary"
                onClick={async () => {
                  const c = await call("/settings/notifications");
                  open(
                    "通知渠道",
                    [
                      {
                        key: "email_enabled",
                        label: "启用邮件",
                        type: "checkbox",
                        value: c.email?.enabled,
                      },
                      {
                        key: "host",
                        label: "SMTP 主机",
                        value: c.email?.host,
                        required: false,
                      },
                      {
                        key: "port",
                        label: "STARTTLS 端口",
                        value: c.email?.port || 587,
                        type: "number",
                      },
                      {
                        key: "username",
                        label: "SMTP 用户名",
                        value: c.email?.username,
                        required: false,
                      },
                      {
                        key: "password",
                        label: "SMTP 新密码（留空保留）",
                        type: "password",
                        required: false,
                      },
                      {
                        key: "from",
                        label: "发件邮箱",
                        value: c.email?.from,
                        required: false,
                      },
                      {
                        key: "to",
                        label: "收件邮箱（逗号分隔）",
                        value: c.email?.to?.join(","),
                        required: false,
                      },
                      {
                        key: "teams_enabled",
                        label: "启用 Teams",
                        type: "checkbox",
                        value: c.teams?.enabled,
                      },
                      {
                        key: "url",
                        label: "Teams 新 Webhook URL（留空保留）",
                        type: "password",
                        required: false,
                      },
                    ],
                    (d) =>
                      call("/settings/notifications", "PUT", {
                        email: {
                          enabled: d.email_enabled,
                          host: d.host,
                          port: Number(d.port),
                          username: d.username,
                          password: d.password,
                          from: d.from,
                          to: d.to
                            .split(",")
                            .map((x: string) => x.trim())
                            .filter(Boolean),
                        },
                        teams: { enabled: d.teams_enabled, url: d.url },
                      }),
                  );
                }}
              >
                邮件 / Teams
              </button>
            </div>
          </section>
        </>
      )}
      {form && (
        <div className="modal-wrap">
          <section className="modal" role="dialog" aria-modal="true">
            <header>
              <h2>{form.title}</h2>
              <button
                className="icon-button"
                aria-label="关闭"
                onClick={() => setForm(null)}
              >
                <X size={18} />
              </button>
            </header>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                setError("");
                const d = Object.fromEntries(new FormData(e.currentTarget));
                for (const f of form.fields)
                  if (f.type === "checkbox")
                    d[f.key] = (
                      e.currentTarget.elements.namedItem(
                        f.key,
                      ) as HTMLInputElement
                    ).checked as any;
                try {
                  await form.submit(d);
                  setForm(null);
                  setNotice("已保存");
                  setRev((x) => x + 1);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <div className="modal-content">
                {form.fields.map((f) => (
                  <label
                    className={f.type === "checkbox" ? "check-field" : "field"}
                    key={f.key}
                  >
                    <span>{f.label}</span>
                    {f.options ? (
                      <select
                        name={f.key}
                        defaultValue={f.value || f.options[0]?.value}
                      >
                        {f.options.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    ) : f.type === "textarea" ? (
                      <textarea
                        name={f.key}
                        defaultValue={f.value}
                        required={f.required !== false}
                      />
                    ) : (
                      <input
                        name={f.key}
                        type={f.type || "text"}
                        defaultValue={
                          f.type === "checkbox" ? undefined : f.value
                        }
                        defaultChecked={
                          f.type === "checkbox" ? !!f.value : undefined
                        }
                        required={f.type !== "checkbox" && f.required !== false}
                        autoComplete={
                          f.type === "password" ? "new-password" : "off"
                        }
                      />
                    )}
                    {f.hint && <small>{f.hint}</small>}
                  </label>
                ))}
                {error && (
                  <div className="error" role="alert">
                    {error}
                  </div>
                )}
              </div>
              <footer className="modal-footer">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setForm(null)}
                >
                  取消
                </button>
                <button className="primary" disabled={busy}>
                  {busy ? "处理中…" : "保存"}
                </button>
              </footer>
            </form>
          </section>
        </div>
      )}
      {detail && (
        <div className="modal-wrap">
          <section className="modal" role="dialog">
            <header>
              <h2>详情</h2>
              <button className="icon-button" onClick={() => setDetail(null)}>
                <X size={18} />
              </button>
            </header>
            <div className="modal-content">
              {detail.users ? (
                detail.users.map((u: R) => (
                  <div className="detail-line" key={u.id}>
                    <div>
                      {u.name}
                      <small>
                        {u.email} · {u.role}
                      </small>
                    </div>
                    <div className="row-actions">
                      <button
                        disabled={u.id === user.id}
                        onClick={async () => {
                          await action("/users/" + u.id, "PUT", {
                            active: !u.active,
                          });
                          setDetail({ users: await call("/users") });
                        }}
                      >
                        {u.active ? "停用" : "启用"}
                      </button>
                      <button
                        onClick={() => {
                          setDetail(null);
                          open(
                            "重置密码",
                            [
                              {
                                key: "password",
                                label: "新密码（至少12位）",
                                type: "password",
                              },
                            ],
                            (d) => call("/users/" + u.id, "PUT", d),
                          );
                        }}
                      >
                        重置密码
                      </button>
                    </div>
                  </div>
                ))
              ) : (
                <>
                  <dl className="details">
                    {Object.entries(detail)
                      .filter(([k, v]) => typeof v !== "object")
                      .map(([k, v]) => (
                        <>
                          <dt key={k}>{k}</dt>
                          <dd>{String(v)}</dd>
                        </>
                      ))}
                  </dl>
                  {detail.checkpoint && (
                    <>
                      <h4>已发布分区与检查点</h4>
                      <pre className="ops-evidence">
                        {JSON.stringify(detail.checkpoint, null, 2)}
                      </pre>
                    </>
                  )}
                  {detail.evidence && (
                    <pre className="ops-evidence">
                      {JSON.stringify(detail.evidence, null, 2)}
                    </pre>
                  )}
                  {detail.deliveries?.map((d: R) => (
                    <div className="detail-line" key={d.id}>
                      <span>
                        {d.channel} · {d.state} · 尝试 {d.attempts} 次
                      </span>
                      {d.state === "FAILED" && (
                        <button
                          className="text-button"
                          onClick={() =>
                            action("/notifications/" + d.id + "/retry")
                          }
                        >
                          重试发送
                        </button>
                      )}
                    </div>
                  ))}
                </>
              )}
            </div>
          </section>
        </div>
      )}
    </>
  );
}
function Connection({
  selected,
  refresh,
  onAction,
}: {
  selected: string;
  refresh: number;
  onAction: () => void;
}) {
  const [s, setS] = useState<R>({});
  useEffect(() => {
    const load = () =>
      call("/azure/states").then((v) =>
        setS(v.find((r: R) => r.id === selected) || {}),
      );
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [selected, refresh]);
  return (
    <div className="modal-content">
      <div className="ops-stages">
        {["auth", "discovery", "cost", "sync"].map((k, i) => (
          <div key={k}>
            <small>
              {i + 1}. {["身份认证", "订阅发现", "成本权限", "消费同步"][i]}
            </small>
            <Tag value={s[k]} />
          </div>
        ))}
      </div>
      {s.error && <div className="error mt">{s.error}</div>}
      <small className="muted">最近同步：{s.last_sync || "尚未同步"}</small>
    </div>
  );
}
