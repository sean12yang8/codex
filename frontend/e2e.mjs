import { chromium } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
const out = "/tmp/azure-lens-e2e";
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  headless: true,
  args: ["--no-sandbox"],
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto(process.env.BASE_URL || "http://127.0.0.1:8000");
  await page.getByRole("heading", { name: "欢迎回来" }).waitFor();
  await page.screenshot({ path: out + "/login.png" });
  await page.getByLabel("工作邮箱").fill("demo@azure-lens.local");
  await page.getByLabel("密码", { exact: true }).fill("AzureLens-Demo-2026!");
  await page.getByRole("button", { name: "登录工作空间" }).click();
  await page.getByRole("heading", { name: "消费趋势" }).waitFor();
  await page.locator(".rank-item").first().waitFor();
  await page.screenshot({ path: out + "/dashboard.png", fullPage: true });
  await page.getByRole("button", { name: "客户管理", exact: true }).click();
  await page.getByRole("button", { name: "新建客户" }).click();
  const name = "E2E 客户 " + Date.now();
  await page.getByLabel("客户名称", { exact: true }).fill(name);
  await page.getByLabel("生效日期（UTC）").fill("2026-01-01");
  await page.getByRole("button", { name: "确认保存" }).click();
  await page.getByRole("button", { name: "租户管理", exact: true }).click();
  await page.getByRole("button", { name: "添加租户", exact: true }).click();
  await page.getByLabel("所属客户").selectOption({ label: name });
  await page.getByLabel("租户名称").fill(name + " Tenant");
  const tenantId = crypto.randomUUID(),
    subId = crypto.randomUUID();
  await page.getByLabel("Tenant ID", { exact: true }).fill(tenantId);
  await page.getByRole("button", { name: "确认保存" }).click();
  await page.getByRole("button", { name: "订阅管理", exact: true }).click();
  await page.getByRole("button", { name: "添加订阅", exact: true }).click();
  await page
    .getByLabel("所属租户")
    .selectOption({ label: name + " / " + name + " Tenant" });
  await page.getByLabel("订阅显示名称").fill(name + " Subscription");
  await page.getByLabel("Subscription ID", { exact: true }).fill(subId);
  await page.getByRole("button", { name: "确认保存" }).click();
  await page.getByRole("button", { name: "系统设置", exact: true }).click();
  await page.getByRole("tab", { name: "数据导入", exact: true }).click();
  await page.getByRole("button", { name: "导入 CSV", exact: true }).click();
  await page.getByLabel("目标订阅").selectOption(subId);
  const day = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  await page.getByLabel("覆盖开始（UTC）").fill(day);
  await page.getByLabel("覆盖结束（UTC）").fill(day);
  await page
    .getByLabel("CSV 文件", { exact: true })
    .setInputFiles({
      name: "e2e-cost.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "subscription_id,usage_date,service,currency,amount\n" +
          subId +
          "," +
          day +
          ",Compute,USD,100\n",
      ),
    });
  await page
    .getByRole("checkbox", { name: "已核实原始成本为客户折扣前基数" })
    .check();
  await page
    .getByRole("checkbox", { name: "声明覆盖日期内全部服务数据完整" })
    .check();
  await page
    .getByLabel("核实依据 / 完整度证据")
    .fill("E2E synthetic fixture, one complete day, pre-discount basis");
  await page.getByRole("button", { name: "校验并导入" }).click();
  await page.getByRole("cell", { name: "e2e-cost.csv" }).first().waitFor();
  await page.getByRole("button", { name: "数据总览", exact: true }).click();
  await page
    .getByLabel("客户筛选", { exact: true })
    .selectOption({ label: name });
  await page.getByLabel("日期范围").selectOption("7");
  await page.getByText("$85.00", { exact: true }).first().waitFor();
  await page.reload();
  await page.getByRole("heading", { name: "消费趋势" }).waitFor();
  await page.locator(".rank-item").first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector(".sidebar").getBoundingClientRect().right <= 1);
  await page.screenshot({ path: out + "/mobile.png", fullPage: true });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
    "mobile must not overflow",
  );
  await page.getByRole("button", { name: "导航菜单" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.getByRole("heading", { name: "欢迎回来" }).waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "PASS: login → create customer/tenant/subscription → CSV import → $85 dashboard → session reload → mobile → logout.",
  );
  console.log("Screenshots: " + out);
} finally {
  await browser.close();
}
