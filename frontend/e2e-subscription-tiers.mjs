/** Synthetic browser metadata and mocked read queue; localhost only, no Azure writes. */
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const base=process.env.BASE_URL||'http://127.0.0.1:8001';assert(['127.0.0.1','localhost'].includes(new URL(base).hostname));
const b=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox']});const p=await b.newPage({viewport:{width:1600,height:1050}});const errors=[];p.on('pageerror',e=>errors.push(e.message));
try{
 await p.goto(base);await p.getByLabel('工作邮箱').fill('demo@azure-lens.local');await p.getByLabel('密码',{exact:true}).fill('AzureLens-Demo-2026!');await p.getByRole('button',{name:'登录工作空间'}).click();await p.getByRole('heading',{name:'消费趋势'}).waitFor();
 const fixture=await p.evaluate(async()=>{
  const post=async(path,body)=>{const r=await fetch('/api/v1'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error('Local fixture failed');return r.json()};
  const c=await post('/customers',{name:'Tier 浏览器验收 '+Date.now(),factor:'1',effective_date:'2026-01-01'});
  const t=await post('/tenants',{id:crypto.randomUUID(),customer_id:c.id,name:'Tier 测试租户'});const subs=[];
  for(const name of ['Tier 标准订阅','Tier 免费订阅','Tier 权限订阅'])subs.push(await post('/subscriptions',{id:crypto.randomUUID(),tenant_id:t.id,name}));
  return {customer:c,subs};
 });
 let refreshed=false,deny=false;
 const first=fixture.subs[0],free=fixture.subs[1],restricted=fixture.subs[2];
 await p.route('**/api/v1/subscriptions',async route=>{
  let r;try{r=await route.fetch({timeout:10000})}catch{await route.abort();return}const rows=await r.json();
  for(const row of rows){
   if(row.id===first.id)row.tier={name:refreshed?'Tier 2':'Tier 1',state:'SUCCEEDED',last_success:'2026-11-11T04:30:00+00:00'};
   if(row.id===free.id)row.tier={name:'Free-Tier',state:'SUCCEEDED'};
   if(row.id===restricted.id)row.tier={name:null,state:'FAILED',error:'TIER_ACCESS_DENIED'};
  }
  await route.fulfill({status:r.status(),contentType:'application/json',body:JSON.stringify(rows)});
 });
 await p.route('**/api/v1/subscriptions/'+first.id+'/tier/refresh',async route=>{
  if(deny)return route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({detail:{message:'无法读取订阅层级'}})});
  refreshed=true;await route.fulfill({status:202,contentType:'application/json',body:JSON.stringify({job_id:'local-tier-read-fixture',state:'QUEUED'})});
 });
 await p.reload();await p.getByRole('button',{name:'订阅管理',exact:true}).click();await p.getByLabel('订阅客户筛选').selectOption(fixture.customer.id);
 await p.locator('thead').getByText('当前层级',{exact:true}).waitFor();
 const row=p.locator('tbody tr').filter({hasText:first.id});await row.getByText('Tier 1',{exact:true}).waitFor();
 await p.locator('tbody tr').filter({hasText:free.id}).getByText('Free Tier',{exact:true}).waitFor();
 await p.locator('tbody tr').filter({hasText:restricted.id}).getByText('无读取权限',{exact:true}).waitFor();
 await row.getByRole('button',{name:'刷新层级：'+first.name,exact:true}).click();await row.getByText('Tier 2',{exact:true}).waitFor();
 await fs.mkdir('/tmp/azurelens-r12-ui',{recursive:true});await p.screenshot({path:'/tmp/azurelens-r12-ui/tiers.png',fullPage:true});
 deny=true;await row.getByRole('button',{name:'刷新层级：'+first.name,exact:true}).click();await p.getByRole('alert').filter({hasText:'无法读取订阅层级'}).waitFor();
 assert.equal(await row.getByText('Tier 2',{exact:true}).count(),1);
 await p.setViewportSize({width:390,height:844});await p.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=0);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 await p.screenshot({path:'/tmp/azurelens-r12-ui/mobile.png',fullPage:true});assert.deepEqual(errors,[]);
 console.log('PASS: current Tier column, Free Tier formatting, permission state, isolated refresh, retained value after failure, and mobile layout.');
}catch(e){console.log('Tier UI check failed:',String(e.message).split('Call log:')[0]);await p.screenshot({path:'/tmp/azurelens-tier-debug.png',fullPage:true});throw new Error('Subscription tier UI assertion failed; inspect the local screenshot')}finally{await p.unrouteAll({behavior:'wait'});await b.close()}
