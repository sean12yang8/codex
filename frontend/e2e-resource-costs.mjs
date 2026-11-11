/** Local synthetic fixtures only: never point this script at the production site. */
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const base=process.env.BASE_URL||'http://127.0.0.1:8001';
assert(['127.0.0.1','localhost'].includes(new URL(base).hostname),'Local demo environment only');
const fixture=JSON.parse(await fs.readFile(process.env.RESOURCE_FIXTURE||'/tmp/azurelens-resource-browser-fixture.json','utf8'));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
const p=await browser.newPage({viewport:{width:1440,height:1050}});
const errors=[];p.on('pageerror',e=>errors.push(e.message));
try{
 await p.goto(base);await p.getByLabel('工作邮箱').fill('demo@azure-lens.local');await p.getByLabel('密码',{exact:true}).fill('AzureLens-Demo-2026!');await p.getByRole('button',{name:'登录工作空间'}).click();
 const stats=p.getByRole('region',{name:'订阅消费统计'});await stats.waitFor();
 await p.getByLabel('搜索订阅消费').fill(fixture.sid);
 const row=stats.locator('tbody tr').filter({hasText:fixture.sid});await row.getByText('US$300.00 USD',{exact:true}).waitFor();
 await row.getByText('US$255.00',{exact:true}).waitFor();assert.equal(await p.getByRole('heading',{name:'最近数据导入'}).count(),0);
 await row.getByRole('button',{name:'查看资源'}).click();await p.getByRole('heading',{name:'资源详情',exact:true}).waitFor();
 assert.equal(await p.getByLabel('资源订阅',{exact:true}).inputValue(),fixture.sid);
 assert.equal(await p.locator('.resource-reconciliation').count(),0);
 const vm=p.locator('tbody tr').filter({hasText:'vm-cost-detail'});await vm.getByText('US$290.00 USD',{exact:true}).waitFor();await vm.getByRole('button',{name:'每日明细'}).click();
 const daily=p.getByRole('region',{name:'资源每日费用明细'});await daily.getByText(fixture.day,{exact:true}).waitFor();await daily.getByText(/^US\$246\.50/).waitFor();
 assert((await daily.textContent()).includes('0.85 / 1'));
 await fs.mkdir('/tmp/azure-lens-resource-e2e',{recursive:true});await p.locator('.table-scroll').evaluateAll(els=>els.forEach(el=>el.scrollLeft=0));await p.screenshot({path:'/tmp/azure-lens-resource-e2e/desktop.png',fullPage:true});
 await p.getByRole('button',{name:'关闭资源明细'}).click();
 await p.getByLabel('搜索资源').fill('未分配');await vm.waitFor({state:'hidden'});await p.locator('tbody tr').getByText('US$10.00 USD',{exact:true}).waitFor();assert.equal(await p.locator('tbody tr').count(),1);
 await p.getByLabel('搜索资源').fill('');await p.getByLabel('资源订阅',{exact:true}).selectOption(fixture.empty);
 await p.getByText('当前区间暂无已采集资源费用',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'每日明细'}).count(),0);
 await p.getByLabel('资源订阅',{exact:true}).selectOption(fixture.sid);await vm.getByText('US$290.00 USD',{exact:true}).waitFor();
 await p.setViewportSize({width:390,height:844});await p.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=0);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 await p.screenshot({path:'/tmp/azure-lens-resource-e2e/mobile.png',fullPage:true});assert.deepEqual(errors,[]);
 console.log('PASS: subscription breakdown, resource navigation, native/USD costs, unallocated fees, day-level discount/FX, no-data switching and mobile layout.');
}finally{await browser.close()}
