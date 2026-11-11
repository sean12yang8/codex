# Azure OpenAI 当前层级（R12）

订阅管理在 Subscription ID 后新增“当前层级”。读取微软返回的当前层级，显示 Tier 1、Tier 2、Free Tier 等；不使用 S0/SKU、下一层级或本平台消费金额推断。

## 官方接口

```http
GET https://management.azure.com/subscriptions/{subscriptionId}/providers/Microsoft.CognitiveServices/quotaTiers/default?api-version=2025-12-01
```

显示字段为 `properties.currentTierName`，保留 `assignmentDate` 与最近成功读取时间。严格核对响应的订阅资源 ID 和资源类型；空值、结构异常或订阅不匹配不发布。未取得层级保持未读取/暂无层级等实际状态，不设默认 Tier 1。

- 用户提供的 [Azure OpenAI 配额文档](https://learn.microsoft.com/en-us/azure/foundry/openai/quotas-limits?tabs=bash%2Ctier1) 在当前执行环境受访问策略限制。
- 已交叉核对微软公开的 [Quota Tier 官方类型定义](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/cognitiveservices/resource-manager/Microsoft.CognitiveServices/CognitiveServices/QuotaTier.tsp)、[GET 示例](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/cognitiveservices/resource-manager/Microsoft.CognitiveServices/CognitiveServices/examples/2025-12-01/GetQuotaTier.json) 和 [Python SDK](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/cognitiveservices/azure-mgmt-cognitiveservices/azure/mgmt/cognitiveservices/operations/_operations.py)。
- 现有租户 SP 已对三个真实订阅读取成功，均返回 Tier 1。查询不修改 Azure 层级、升级策略、RBAC 或配额。

## 更新与状态

成功发现订阅后，验证、手动同步、每日同步和补充同步任务会排队独立的层级读取。自动任务对 15 分钟内已读取结果去重；人工验证/同步和层级列的刷新按钮强制排队（同一活跃请求仍幂等）。页面沿用 15 秒刷新周期。

API 使用现有租户 SP，需拥有订阅范围 `Microsoft.CognitiveServices/quotaTiers/read`（例如包含该读取操作的角色）；成本可读并不自动证明该操作可读。权限不足、404、凭据缺失、限流和结构异常分别记录；限流沿用原四次尝试和 Retry-After 策略。

读取失败独立于成本状态，保留上次成功层级并显示更新失败图标；从未成功读取时显示实际失败状态。任务可在系统设置 → 同步中心查看、重试和取消。

当前层级按订阅单独存储；不触发财务数据版本、折扣/汇率重算、费用汇总或业务告警。永久删除通过订阅 FK 级联清除层级元数据，删除墓碑重放也适用。
