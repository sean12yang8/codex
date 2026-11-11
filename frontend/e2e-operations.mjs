import { chromium } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
const b = await chromium.launch({
  executablePath: "/usr/bin/chromium",
  headless: true,
  args: ["--no-sandbox"],
});
const p = await b.newPage({ viewport: { width: 1440, height: 1050 } });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
try {
  await p.goto(process.env.BASE_URL || "http://127.0.0.1:8010");
  await p.getByLabel("工作邮箱").fill("demo@azure-lens.local");
  await p.getByLabel("密码", { exact: true }).fill("AzureLens-Demo-2026!");
  await p.getByRole("button", { name: "登录工作空间" }).click();
  assert.equal(await p.locator("aside nav").getByRole("button", { name: "同步中心", exact: true }).count(), 0);
  assert.equal(await p.locator("aside nav").getByRole("button", { name: "数据导入", exact: true }).count(), 0);
  await p.getByRole("button", { name: "系统设置", exact: true }).click();
  const general = p.getByRole("tab", { name: "基础设置", exact: true });
  assert.equal(await general.getAttribute("aria-selected"), "true");
  await general.focus();await p.keyboard.press("ArrowRight");
  assert.equal(await p.getByRole("tab", { name: "数据导入", exact: true }).getAttribute("aria-selected"), "true");
  await p.getByRole("heading", { name: "导入历史", exact: true }).waitFor();
  assert.equal(await p.getByRole("button", { name: "新增汇率", exact: true }).count(), 0);
  await p.keyboard.press("ArrowRight");
  assert.equal(await p.getByRole("tab", { name: "同步中心", exact: true }).getAttribute("aria-selected"), "true");
  await p.getByRole("heading", { name: "同步任务中心" }).waitFor();
  await p.getByRole("button", { name: "Azure 接入", exact: true }).click();
  await p.getByRole("heading", { name: "Azure 接入与四阶段校验" }).waitFor();
  await p.getByRole("button", { name: "告警中心", exact: true }).click();
  await p.getByRole("button", { name: "规则设置" }).click();
  await p.getByRole("button", { name: "保存", exact: true }).click();
  await p.getByRole("dialog").waitFor({ state: "hidden" });
  await p.getByRole("button", { name: "报表中心", exact: true }).click();
  await p.getByRole("button", { name: "新建导出" }).click();
  await p.getByRole("button", { name: "保存", exact: true }).click();
  await p
    .getByRole("link", { name: "安全下载" })
    .first()
    .waitFor({ timeout: 30000 });
  await p.getByRole("button", { name: "系统设置", exact: true }).click();
  await p.getByRole("button", { name: "创建用户", exact: true }).click();
  await p
    .getByLabel("邮箱", { exact: true })
    .fill("e2e-" + Date.now() + "@example.com");
  await p.getByLabel("显示名称").fill("E2E Member");
  await p.getByLabel("初始密码（至少12位）").fill("E2E-Test-Only-Password");
  await p.getByRole("button", { name: "保存", exact: true }).click();
  await p.getByRole("dialog").waitFor({ state: "hidden" });
  await p.getByRole("button", { name: "自动同步计划" }).click();
  await p.getByRole("button", { name: "保存", exact: true }).click();
  await p.getByRole("dialog").waitFor({ state: "hidden" });
  await fs.mkdir("/tmp/azurelens-r11-ui", { recursive: true });
  await p.getByRole("tab", { name: "同步中心", exact: true }).click();
  await p.getByRole("heading", { name: "同步任务中心" }).waitFor();
  await p.screenshot({path:"/tmp/azurelens-r11-ui/jobs.png",fullPage:true});
  await p.getByRole("tab", { name: "数据导入", exact: true }).click();
  await p.getByRole("button", { name: "导入 CSV", exact: true }).waitFor();
  await p.screenshot({path:"/tmp/azurelens-r11-ui/imports.png",fullPage:true});
  await p.setViewportSize({width:390,height:844});
  await p.waitForFunction(()=>document.querySelector(".sidebar").getBoundingClientRect().right<=0);
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await p.getByRole("tab", { name: "同步中心", exact: true }).click();
  await p.getByRole("heading", { name: "同步任务中心" }).waitFor();
  await p.screenshot({path:"/tmp/azurelens-r11-ui/mobile.png",fullPage:true});
  await p.setViewportSize({width:1440,height:1050});
  await p.waitForFunction(()=>document.querySelector(".sidebar").getBoundingClientRect().left>=0);
  await p.getByRole("button", { name: "归属与删除", exact: true }).click();
  await p.getByRole("heading", { name: "订阅归属与永久删除" }).waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "PASS: jobs, Azure stages, alert rules, async XLSX export, user creation, schedule and governance UI.",
  );
} finally {
  await b.close();
}
