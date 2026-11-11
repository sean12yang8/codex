# AzureLens — Azure 客户 ACR 监控平台

React 19 / TypeScript / Tailwind CSS 4 / FastAPI / PostgreSQL。中文界面，Docker Compose 独立运行 API、Worker 与数据库，使用 Caddy HTTPS 部署于 Azure VM。

**当前为 v0.2 功能实现版。完整 PRD 的生产验收仍需真实数据、外部身份/通知配置及恢复/吞吐证据。** 具体实现与未通过的门槛见 [PRD 验收台账](docs/prd-implementation-matrix.md)，运维方式见 [v0.2 运维说明](docs/operations-v02.md)。不得将“功能已编码”描述为“全部真实 PoC 已通过”。

## 功能

- 密码登录、管理员/成员、用户启停/会话撤销/密码重置、Entra OIDC SSO 与预授权身份绑定。
- 客户、历史折扣、Tenant/SP、Subscription CRUD，1–3 组 Tenant/SP 一次接入，凭据加密与轮换。
- 自动认证、发现订阅、逐订阅读取 Actual Cost；独立四阶段状态，缺失订阅不删除。
- 持久化优先队列、历史回填、每日 UTC 计划、分区检查点、重试/取消、任务详情。
- 标准与两种明确映射 Azure CSV，严格 Blob Manifest/分片校验，只读 Export + API 回退、有效版本与幂等发布。
- 按消费日匹配折扣、FX 和订阅历史归属；Decimal / PostgreSQL NUMERIC 精确计算。
- Dashboard 日期/对象筛选、日/月趋势、Top5、累计、环比、完整度与异常对象；版本化查询缓存随财务写入失效。
- 同步失败、波动、完整零消费告警，读/确认/静音/关闭/恢复，独立 Email / Teams 投递记录和重试。
- 用户、SSO、SMTP/Teams、UTC 计划、阈值覆盖、FX 与审计设置。
- 后台 Excel/CSV，客户/租户/订阅/服务日月汇总、安全下载、公式注入防护。
- 删除影响预览/二次确认、级联删除、跨客户历史定向删除、独立数据库外墓碑与恢复重放。生产删除默认关闭，须完成独立保留及完整 VM 恢复后批准启用。

## 最新功能

- 数据总览按客户/租户展示订阅消费统计，可跳转至资源级费用和每日明细。
- 原币成本、内部 ACR 与已核验口径分别展示；历史折扣版本和汇率按消费日匹配。
- 订阅管理显示 Azure OpenAI 当前层级，读取官方 `quotaTiers/default`，支持自动更新与手动刷新。
- 数据导入与同步中心统一位于系统设置，与基础设置以标签页切换。
- 已完成 SQLite / PostgreSQL 各 52 项测试（各另有 1 项数据库专用场景跳过），并通过 Azure VM 部署、真实成本/层级读取及浏览器回归。详细证据见 [验证记录](docs/validation.md)。

## 本地开发

需要 Python 3.12、Node.js 24。使用现有 checkout；云任务已隔离，无需额外创建 Git worktree。

```bash
bash scripts/setup.sh
.venv/bin/python -m backend.manage create-user --email admin@example.com --name 管理员
# 终端一：API / 静态前端
bash scripts/start.sh
# 终端二：队列、调度、告警、通知和导出
.venv/bin/python -m backend.worker
```

前端热更新：`cd frontend && npm run dev`；Vite 转发 `/api` 至 8000。SSO 需真实 HTTPS PUBLIC_ORIGIN。

空数据库仅作开发演示时，可以使用 `python -m backend.manage demo`，演示账号 `demo@azure-lens.local` / `AzureLens-Demo-2026!`。演示数据与密码是公开测试夹具，禁止部署到真实环境。命令拒绝覆盖非空数据库。生产实例无预置演示数据。

## Linux / Compose

```bash
cp .env.example .env
# 填写随机 URL 安全的 POSTGRES_PASSWORD；禁止提交 .env。
docker compose up -d --build
docker compose exec app python -m backend.manage create-user --email admin@example.com
```

默认仅绑定主机 127.0.0.1:8000；生产由 Caddy/Nginx 提供 HTTPS，设置 `PUBLIC_ORIGIN=https://域名`、`COOKIE_SECURE=true`。API 与 Worker 共享 app-data 加密主密钥，PostgreSQL 使用独立卷。不要随意更换或丢弃 `credential.key`。

云代理构建使用可信 CA BuildKit secret，始终保留 TLS 验证：

```bash
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t azure-lens .
```

Azure 运行实例信息见 [部署记录](docs/deployment-azure.md)。登录 Azure CLI 仅用于部署，不会将用户授权令牌注入平台或替代客户 SP。

## 数据接入

添加 SP 凭据后自动排队；也可在 Azure 接入点击验证/同步。SP 必须已在 Azure 侧获得正确的订阅发现、Cost Management 与所需 Blob 读取权限。平台不自动授予客户 RBAC，也不创建客户 Export/Storage。

平台 CSV 模板：

```csv
subscription_id,usage_date,service,currency,amount
20000000-0000-4000-8000-000000000001,2026-11-01,Virtual Machines,USD,100.00
20000000-0000-4000-8000-000000000001,2026-11-02,Virtual Machines,USD,0
```

原始成本使用 Decimal，最多 60 位小数；折扣和汇率等配置最多 12 位小数。

UTF-8（可带 BOM），10 MB / 50,000 行 / 367 天上限。每批一个订阅，每个有记录的 UTC 日代表全服务快照；修正必须提交完整日并填写原因，不上传差额。同一文件与声明幂等；范围外记录保留。Azure CSV 映射和 Blob 合同见验收台账，未知格式拒绝入账。

成本口径需管理员提供折扣前基数核实证据。缺少 FX、折扣或基数未核实的费用不伪装为 0。API 空结果不自动证明零消费，完整度未知时不触发零消费告警。USD 固定 1，时间按 UTC 消费日/自然月，历史累计不随日期筛选变化。

## 验证

```bash
.venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build
BASE_URL=http://127.0.0.1:8000 npm run test:e2e
BASE_URL=http://127.0.0.1:8000 node e2e-operations.mjs
```

浏览器测试需要运行带演示夹具的专用开发实例和 Worker，使用 `/usr/bin/chromium`，可通过 `CHROMIUM_PATH` 覆盖。测试会创建 E2E 对象，不得对生产管理员账号运行演示测试。

基准工具 `BENCHMARK_ISOLATED=yes python -m backend.benchmark` 仅允许专用空数据库，生成 1,002 客户、10,020 订阅、160,320 有效费用、20 并发混合负载。其日期长度、冷查询、缓存状态及混合比例必须一起解释，不能替代全部生产负载与采集吞吐验收。
