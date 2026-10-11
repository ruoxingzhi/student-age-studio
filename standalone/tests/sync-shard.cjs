'use strict';
// Shard math parity with sync_store.shard_interval + allocate() shard clamping.
// No browser, no git: record-ids.js loads in a tiny DOM stub.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function element(){return {setAttribute(){},addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];},focus(){},select(){}};}
const sandbox={window:{STUDIO_TOKEN:'t'},document:{createElement:element,body:{append(){}},addEventListener(){}},location:{hash:''},crypto,structuredClone,setTimeout,clearTimeout};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../record-ids.js'),'utf8'),sandbox);
const IDS=sandbox.window.STUDIO_IDS;
// Same vectors as test_sync_team.py:VECTORS.
for(const [start,end,step,shard,lo,hi] of [[1000000,1999999,1,0,1000000,1000976],[1000000,1999999,1,1,1000977,1001953],[1000000,1999999,1,1023,1999471,1999999],[1000001,1999999,1000,5,1005001,1005001],[3000000,3999999,1,1023,3999471,3999999]])
  assert.deepEqual([...IDS.shardRange(start,end,step,shard,1024)],[lo,hi],`shard ${shard}`);
// Solo (no syncShard): unchanged behavior, full range, derived blocks intact.
IDS.configure({tables:{}});
assert.ok(IDS.allocate('ItemCfg',{})>=1000000);
const talk=IDS.allocate('TalkCfg',{},7);
assert.ok(talk>=7001&&talk<=7999,'derived untouched: '+talk);
// Teamed shard 5: every ItemCfg id lands in [1004885,1005861].
IDS.configure({tables:{},syncShard:{active:true,shard:5,shards:1024}});
for(let i=0;i<20;i++){const id=IDS.allocate('ItemCfg',{});assert.ok(id>=1004885&&id<=1005861,'out of shard: '+id);}
// Derived tables ignore the shard (owners are already partitioned).
const talk2=IDS.allocate('TalkCfg',{},7);
assert.ok(talk2>=7001&&talk2<=7999,'derived untouched when teamed: '+talk2);
// Exhausted shard raises the Chinese reroll message instead of wandering off.
IDS.configure({tables:{ItemCfg:Array.from({length:977},(_,i)=>1004885+i)},syncShard:{active:true,shard:5,shards:1024}});
assert.throws(()=>IDS.allocate('ItemCfg',{}),/号段/);
console.log('SYNC_SHARD_OK');
