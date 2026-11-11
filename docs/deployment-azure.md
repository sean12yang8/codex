# Azure VM 部署记录

目标订阅：`b54df7e2-94a3-4d20-946a-06a42edc45d2`。

- 独立资源组：`rg-azurelens-prod`，区域 East Asia。
- VM：`vm-azurelens-prod`，Ubuntu 24.04 LTS；D2as_v5（2 vCPU / 8 GB），原 B2ms 由于订阅容量限制不可用。
- 公网 IP：`20.205.97.241`。
- 系统盘：64 GB Standard SSD。
- DNS：`azurelens-6d8d061f.eastasia.cloudapp.azure.com`。
- Caddy 为应用申请/自动续签 HTTPS 证书；仅 HTTP 80（证书签发/重定向）与 HTTPS 443 对公网开放。
- SSH 不开放公网；通过 Azure VM Run Command 进行管理。部署 SSH 私钥不进入源码或镜像。
- Docker Compose：应用仅绑定 `127.0.0.1:8000`；PostgreSQL 无主机端口；独立持久化数据库与凭据加密主密钥。
- Cookie Secure 开启，PUBLIC_ORIGIN 为 HTTPS 域名；无演示数据和公开演示账号。

## 初始管理员

默认用户名为 `admin@azurelens.local`。初始密码在 VM 上随机生成，只存于 `/root/azurelens-admin.json`（0600）。部署过程不输出密码。

管理员可以登录 Azure 门户 → VM → 运行命令（Run Command）→ RunShellScript，执行：

```sh
sudo cat /root/azurelens-admin.json
```

在门户中查看并妥善保管密码，不要贴回聊天、Git 或运维日志。

## 运维

应用目录：`/opt/azurelens/app`；敏感配置 `.env`（0600）。

```sh
sudo systemctl status azurelens caddy --no-pager
sudo sh -c 'cd /opt/azurelens/app && docker compose ps'
sudo tail -n 60 /var/log/azurelens-deploy.log
```

`azurelens.service` 开机启动 Compose；容器配置 `unless-stopped`。Caddy 与 Docker 开机自启。

现已升级至 v0.2，包含独立 Worker、Azure API/Blob 适配、自动发现/历史回填、告警、报表、用户/SSO 设置及归属管理。真实 CSP 成本、SSO 和外部通知仍需配置与端到端验收。应用部署不会自动将本次 Azure CLI 管理员登录凭据注入平台，也不会自动读取此订阅的成本。

## 本次实际验证

- VM 已创建：Ubuntu 24.04 LTS / Standard_D2as_v5，SSH 密码认证关闭。
- 源码包 SHA-256 校验通过，Docker 镜像在 VM 本机构建完成。
- 通过 HTTPS 主机名校验证书链、管理员密码登录、Secure Cookie 会话及退出。
- 首次部署为空数据库；v0.2 升级保留了用户随后新增的 1 个客户、1 个租户；无公开演示账号或合成费用。
- 重启整个应用栈后重复上述认证及空数据库检查，全部通过。
- HTTP 返回 308 跳转；Docker、Caddy、应用栈开机启动已启用。
- Azure NSG 自定义入站规则仅允许 Internet → TCP 80/443。

域名由 Caddy 自动管理公开受信证书。首次 v0.2 校验曾返回 AADSTS700016。用户更新配置后，2026-11-10 已通过认证、发现 3 个订阅并证明逐订阅 Cost 可读；修复高精度成本采集后，首次真实成本同步及租户校验已成功，365 天回填在后台继续。成本价格语义仍待核实。详情见 [同步故障与恢复记录](incidents/sync-2026-11-10.md)。部署 Azure CLI 的用户令牌不会用于客户数据采集。

20 并发、10,020 订阅的隔离 API 性能结果及限制见 [验证记录](validation.md)。v0.2 在激活前运行独立 PostgreSQL 测试，并保留升级前备份与回滚镜像；生产永久删除暂不启用，详见 [运维手册](operations-v02.md)。
