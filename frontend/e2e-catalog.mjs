import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
const p=await browser.newPage();
const errors=[];p.on('pageerror',e=>errors.push(e.message));
try {
 await p.goto(process.env.BASE_URL||'http://127.0.0.1:8000');
 await p.getByLabel('工作邮箱').fill('demo@azure-lens.local');
 await p.getByLabel('密码',{exact:true}).fill('AzureLens-Demo-2026!');
 await p.getByRole('button',{name:'登录工作空间'}).click();
 await p.getByRole('heading',{name:'消费趋势'}).waitFor();
 const prefix='分页验收 '+Date.now();
 await p.evaluate(async prefix=>{
  for(let i=0;i<30;i++){
   const res=await fetch('/api/v1/customers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:prefix+' '+String(i).padStart(2,'0'),factor:'1',effective_date:'2026-01-01'})});
   if(!res.ok)throw new Error('Fixture creation failed');
  }
 },prefix);
 await p.reload();
 await p.getByRole('button',{name:'客户管理',exact:true}).click();
 await p.getByLabel('搜索客户').fill(prefix);
 await p.getByText('共 30 条 · 每页 25 条').waitFor();
 await p.waitForFunction(()=>document.querySelectorAll('tbody tr').length===25);
 const first=await p.locator('tbody tr').allTextContents();
 await p.getByRole('button',{name:'下一页',exact:true}).click();
 await p.waitForFunction(()=>document.querySelectorAll('tbody tr').length===5);
 const second=await p.locator('tbody tr').allTextContents();
 assert.equal(new Set([...first,...second]).size,30);
 await p.getByLabel('搜索客户').fill(prefix+' 29');
 await p.getByText('共 1 条 · 每页 25 条').waitFor();
 assert.equal(await p.locator('tbody tr').count(),1);
 assert.deepEqual(errors,[]);
 console.log('PASS: server search, 25+5 stable pagination, no duplicate rows, filter resets page.');
}finally{await browser.close()}
