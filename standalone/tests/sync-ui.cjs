'use strict';
// Sync & collaboration page: sections, relay/save/job/pick/restore/rename flows,
// escaping of collaborator strings, no-overflow of long paths.
// Route fixtures only; no user Mod writes.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});try{
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 const errors=[],calls={save:[],job:0,pick:[],restore:[],relay:[],join:[],rename:[]};
 const status={joined:false,relay:'',members:[],device:{id:'d1',name:'<img src=x onerror=alert(1)>'},pendingPicks:[],timeline:[{'编号':'base1111','时间':'2026-01-01 10:00','作者':'我','说明':'底稿<script>alert(1)</script>'}],snapshotCount:1,state:{'已修改':[],'新增':[],'已删除':[]},sizeWarning:'',revision:'r1',readOnly:false};
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://sync.test/**',async route=>{const url=new URL(route.request().url());
  if(url.pathname==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:'<html><body><div id="wk-sync"></div></body></html>'});
  if(url.pathname==='/sync-ui.js')return route.fulfill({contentType:'text/javascript; charset=utf-8',body:fs.readFileSync(path.join(__dirname,'../sync-ui.js'),'utf8')});
  if(url.pathname==='/api/sync-status')return route.fulfill({json:{...status,projectId:'local:qa'}});
  if(url.pathname==='/api/sync-save'){const data=route.request().postDataJSON();calls.save.push(data);status.timeline.unshift({'编号':'abc12345','时间':'2026-01-02 10:00','作者':'我','说明':data.summary});status.snapshotCount++;status.revision='r2';return route.fulfill({json:{saved:true,revision:'r2'}});}
  if(url.pathname==='/api/sync-relay'){const data=route.request().postDataJSON();calls.relay.push(data);status.joined=true;status.relay=data.path;return route.fulfill({json:{relay:data.path}});}
  if(url.pathname==='/api/sync-join'){const data=route.request().postDataJSON();calls.join.push(data);status.joined=true;return route.fulfill({json:{ok:true,revision:'r2'}});}
  if(url.pathname==='/api/sync-start')return route.fulfill({json:{jobId:'j1',projectId:'local:qa',state:'queued',phase:'准备同步',percent:0}});
  if(url.pathname==='/api/sync-job'){calls.job++;if(calls.job<3)return route.fulfill({json:{jobId:'j1',projectId:'local:qa',state:'running',phase:'正在合稿',percent:45}});status.pendingPicks=[{'文件':'line.txt','对方版本':'x','处理':'两边不一样，已保留你的，对方的存在待选里。','作者':'对方','时间':'现在'}];return route.fulfill({json:{jobId:'j1',projectId:'local:qa',state:'done',phase:'同步完成',percent:100,revision:'r3',report:{'合上':['story.txt'],'待选':['line.txt'],'已发送':true,'说明':['有几处两边改的不一样。']}}});}
  if(url.pathname==='/api/sync-pick'){const data=route.request().postDataJSON();calls.pick.push(data);status.pendingPicks=[];return route.fulfill({json:{ok:true,'剩余待选':0,revision:'r4'}});}
  if(url.pathname==='/api/sync-restore'){const data=route.request().postDataJSON();calls.restore.push(data);return route.fulfill({json:{ok:true,revision:'r5'}});}
  if(url.pathname==='/api/sync-device'){const data=route.request().postDataJSON();calls.rename.push(data);status.device.name=data.name;return route.fulfill({json:{device:status.device}});}
  return route.fulfill({status:404,body:''});});
 await page.goto('http://sync.test/');
 for(const file of ['styles.css','workshop.css','sync.css'])await page.addStyleTag({path:path.join(__dirname,'..',file)});
 await page.addScriptTag({path:path.join(__dirname,'../sync-ui.js')});
 await page.evaluate(()=>{window.__mod={host:document.querySelector('#wk-sync'),api:async(path,data)=>{const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error||'no');return v;},mod:{id:'local:qa',name:'协作测试模组'}};});
 await page.evaluate(async()=>{const m=window.__mod;window.__notices=[];const ui=StudentAgeSync.create(m.host,{project:m.mod,api:m.api,token:'',status:(t,e)=>{window.__notices.push([t,!!e]);},home:async()=>{},onState:()=>{},onSaved:async()=>{}});window.__ui=ui;await ui.load();});
 await page.locator('.sync-wrap').waitFor();
 assert.match(await page.locator('.sync-head h2').textContent(),/同步协作/);
 assert.equal(await page.locator('.sync-badge').textContent(),'未加入同步');
 // Relay setup.
 await page.locator('[data-sync-input="relayPath"]').fill('D:\\网盘\\拾光工坊同步');
 await page.locator('[data-sync="relay"]').click();
 await page.locator('.sync-badge:has-text("已连接")').waitFor();
 assert.deepEqual(calls.relay,[{projectId:'local:qa',path:'D:\\网盘\\拾光工坊同步'}]);
 await page.locator('[data-sync="join"]').click();
 await page.waitForFunction(()=>window.__notices.some(n=>n[0].includes('已加入')));
 assert.deepEqual(calls.join,[{projectId:'local:qa',relay:'D:\\网盘\\拾光工坊同步'}]);
 // Save flow.
 await page.locator('[data-sync-input="summary"]').fill('第一章初稿');
 await page.locator('[data-sync="save"]').click();
 await page.locator('.sync-list .sync-item strong:has-text("第一章初稿")').waitFor();
 assert.deepEqual(calls.save,[{projectId:'local:qa',revision:'r1',summary:'第一章初稿'}]);
 // Collaborator strings never become markup.
 assert.equal(await page.locator('.sync-wrap img').count(),0);
 // Job flow with in-place progress, then picks appear.
 await page.locator('[data-sync="start"]').click();
 await page.locator('[data-sync-progress]:visible').waitFor();
 await page.locator('.sync-result:has-text("同步完成")').waitFor();
 assert.equal(calls.job>=2,true);
 assert.match(await page.locator('.sync-result').textContent(),/story\.txt/);
 await page.locator('.sync-card[aria-label="待选"]').waitFor();
 await page.locator('[data-sync="pick-theirs"]').click();
 await page.waitForFunction(()=>!document.querySelector('.sync-card[aria-label="待选"]'));
 assert.deepEqual(calls.pick,[{projectId:'local:qa',file:'line.txt',choice:'要对方的'}]);
 // Restore uses two clicks, never a native dialog.
 await page.locator('[data-sync="restore"]').first().click();
 await page.locator('[data-sync="restore"]:has-text("再次点击")').waitFor();
 await page.locator('[data-sync="restore"]:has-text("再次点击")').click();
 await page.locator('[data-sync="restore"]:has-text("再次点击")').waitFor({state:'detached'});
 assert.equal(calls.restore.length,1);assert.equal(calls.restore[0].id,'abc12345');
 // Rename device.
 await page.locator('[data-sync-input="deviceName"]').fill('主力机');
 await page.locator('[data-sync="rename"]').click();
 await page.waitForFunction(()=>document.body.textContent.includes('主力机'));
 assert.deepEqual(calls.rename,[{projectId:'local:qa',name:'主力机'}]);
 // Narrow viewport: long paths must wrap, never overflow their cards.
 await page.setViewportSize({width:700,height:800});
 const overflow=await page.evaluate(()=>[...document.querySelectorAll('.sync-item-main strong,.sync-stat strong')].map(el=>el.scrollWidth-el.clientWidth));
 assert.ok(overflow.every(v=>v<=1),'overflow: '+JSON.stringify(overflow));
 if(process.env.UI_SCREENSHOT){fs.mkdirSync(process.env.UI_SCREENSHOT,{recursive:true});await page.screenshot({path:path.join(process.env.UI_SCREENSHOT,'sync-narrow.png')});await page.setViewportSize({width:1600,height:1000});await page.screenshot({path:path.join(process.env.UI_SCREENSHOT,'sync-wide.png')});}
 assert.deepEqual(errors,[]);
 assert.deepEqual(await page.evaluate(()=>window.__notices.filter(n=>n[1]).map(n=>n[0])),[]);
 console.log('PASS: sections, relay/save/job/pick/restore/rename flows, escaping, no-overflow');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
