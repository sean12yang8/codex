import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
const p=await browser.newPage({viewport:{width:1440,height:1050}});
const errors=[];p.on('pageerror',e=>errors.push(e.message));
try{
 await p.goto(process.env.BASE_URL||'http://127.0.0.1:8000');await p.getByLabel('工作邮箱').fill('demo@azure-lens.local');await p.getByLabel('密码',{exact:true}).fill('AzureLens-Demo-2026!');await p.getByRole('button',{name:'登录工作空间'}).click();await p.getByRole('heading',{name:'消费趋势'}).waitFor();
 const fixture=await p.evaluate(async()=>{
  const day=new Date(Date.now()-86400000).toISOString().slice(0,10);
  const r=await fetch('/api/v1/customers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'折扣口径验收 '+Date.now(),factor:'0.85',effective_date:day})});if(!r.ok)throw Error('Fixture creation failed');return {...await r.json(),day};
 });
 await p.reload();await p.getByRole('heading',{name:'消费趋势'}).waitFor();
 await p.getByRole('button',{name:'客户管理',exact:true}).click();await p.getByLabel('搜索客户').fill(fixture.name);
 const row=p.locator('tbody tr').filter({hasText:fixture.name});await row.getByRole('button',{name:'折扣',exact:true}).click();
 const dialog=p.getByRole('dialog');await dialog.locator('input[name=factor]').fill('1');
 const preview=dialog.getByRole('region',{name:'折扣计算口径预览'});
 await preview.getByText(/保存后不会全部按 1 核算/).waitFor();
 const lines=await preview.locator('.discount-policy-table tbody tr').allTextContents();
 assert(lines.some(line=>line.includes('0.85')&&line.includes('历史版本')));
 assert(lines.some(line=>line.includes('当前版本')&&line.includes('1（100%）')));
 await fs.mkdir('/tmp/azure-lens-discount-e2e',{recursive:true});await p.screenshot({path:'/tmp/azure-lens-discount-e2e/preview.png',fullPage:true});
 // Selecting an occupied date gives an immediate explanation and blocks submission.
 await dialog.getByLabel('生效日期（UTC）',{exact:true}).fill(fixture.day);await preview.getByRole('alert').waitFor();
 assert.equal(await dialog.getByRole('button',{name:'保存折扣版本',exact:true}).isDisabled(),true);
 // A future version can be saved while the selected historical interval stays explicit.
 const tomorrow=new Date(Date.now()+86400000).toISOString().slice(0,10);await dialog.getByLabel('生效日期（UTC）',{exact:true}).fill(tomorrow);
 await p.setViewportSize({width:390,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 await dialog.getByRole('button',{name:'保存折扣版本',exact:true}).click();await dialog.waitFor({state:'hidden'});
 await p.getByRole('status').filter({hasText:'已保存折扣版本：系数 1'}).waitFor();
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 const versions=await p.evaluate(async id=>{const data=await(await fetch('/api/v1/customers')).json();return data.find(c=>c.id===id).discounts},fixture.id);
 assert.deepEqual(versions.map(v=>[v.day,v.factor]),[[fixture.day,'0.85'],[tomorrow,'1']]);assert.deepEqual(errors,[]);
 console.log('PASS: live discount policy segments, historical 0.85 retained, trial fallback, duplicate date guard, save notice and mobile.');
}finally{await browser.close()}
