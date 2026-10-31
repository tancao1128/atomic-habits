const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const script=[...fs.readFileSync('index.html','utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].pop()[1];
function setup({host='localhost',fetchImpl,failStorage=false}={}) {
  const elements={};
  const doc={getElementById(id){return elements[id] ||= {textContent:'',value:'',classList:{add(){},remove(){},toggle(){}}}},documentElement:{classList:{add(){},remove(){}}}};
  const map=new Map();
  const ctx=vm.createContext({document:doc,window:{addEventListener(){}},location:{protocol:'http:',hostname:host},localStorage:{getItem:k=>map.get(k)||null,setItem(k,v){if(failStorage)throw Error('quota');map.set(k,v)}},navigator:{},console,fetch:fetchImpl||(()=>{throw Error('unexpected fetch')}),setTimeout(){},clearTimeout(){},Date,JSON,URLSearchParams});
  vm.runInContext(script,ctx);
  vm.runInContext("showToast = () => {}; appState={habits:[{id:'fake'}],logs:{}};",ctx);
  return {ctx,elements,map};
}
const response=(value,ok=true)=>({ok,json:async()=>value,status:ok?200:503});
test('pairing link contains no token and no QR service request',async()=>{
 let copied='';const {ctx}=setup();
 ctx.navigator.clipboard={writeText:async value=>{copied=value}};
 await vm.runInContext('copyIphoneLink()',ctx);
 assert.equal(copied,'https://tancao.org/atomic-habits/');
 assert.doesNotMatch(script,/api\.qrserver\.com|\/api\/pairing|sync_key/);
});
test('local write failure is visible and never reports success',async()=>{
 let called=false;const {ctx,elements}=setup({failStorage:true,fetchImpl:()=>{called=true}});
 assert.equal(await vm.runInContext('saveState()',ctx),false);assert.match(elements['save-status'].textContent,/未|chưa được lưu/i);assert.equal(called,false);
});
test('server cloud failure stays visible after browser save',async()=>{
 const {ctx,elements}=setup({fetchImpl:async()=>response({ok:true,local_saved:true,cloud_synced:false,cloud_error:'Không đẩy được Gist'})});
 assert.equal(await vm.runInContext('saveState()',ctx),false);assert.match(elements['save-status'].textContent,/Không đẩy được Gist/);
});
test('local disk failure and network failure do not claim success',async()=>{
 for(const fetchImpl of [async()=>response({local_saved:false},false),async()=>{throw Error('offline')}]){
  const {ctx,elements}=setup({fetchImpl});assert.equal(await vm.runInContext('saveState()',ctx),false);assert.match(elements['save-status'].textContent,/Lỗi/);
 }
});
test('server verified save is displayed',async()=>{
 const {ctx,elements}=setup({fetchImpl:async()=>response({local_saved:true,cloud_synced:true})});
 assert.equal(await vm.runInContext('saveState()',ctx),true);assert.match(elements['save-status'].textContent,/xác minh/);
});
test('mobile cloud difference never overwrites browser state',async()=>{
 const remote={habits:[{id:'other'}],logs:{}};
 const {ctx,elements,map}=setup({host:'phone.example',fetchImpl:async()=>response({files:{'atomic_habits_state.json':{content:JSON.stringify({state:remote})}}})});
 map.set('atomic_habit_cloud_config_v1',JSON.stringify({endpoint:'https://api.github.com/gists/fake',apiKey:'fake'}));
 vm.runInContext("hadLocalState=true;localStorage.setItem(STORAGE_KEY,JSON.stringify(appState));",ctx);
 const before=map.get('atomic_habit_streak_os_v1');
 assert.equal(await vm.runInContext('pullFromCloud(true)',ctx),false);
 assert.equal(map.get('atomic_habit_streak_os_v1'),before);assert.match(elements['save-status'].textContent,/khác nhau/);
});
test('mobile cloud push failure is shown',async()=>{
 const old={habits:[{id:'fake'}],logs:{}};
 const {ctx,elements,map}=setup({host:'phone.example',fetchImpl:async(url,opts)=>opts?.method==='GET'?response({files:{'atomic_habits_state.json':{content:JSON.stringify({state:old})}}}):response({},false)});
 map.set('atomic_habit_cloud_config_v1',JSON.stringify({endpoint:'https://api.github.com/gists/fake',apiKey:'fake'}));
 vm.runInContext('cloudReference={habits:[{id:"fake"}],logs:{}};appState.logs={today:{fake:true}}',ctx);
 assert.equal(await vm.runInContext('saveState()',ctx),false);assert.match(elements['save-status'].textContent,/Không đẩy được cloud/);
});
test('retry recognizes a previously completed cloud write without overwriting',async()=>{
 let writes=0;const state={habits:[{id:'fake'}],logs:{}};
 const {ctx,map}=setup({host:'phone.example',fetchImpl:async(url,opts)=>{
   if(opts.method!=='GET') writes++;
   return response({files:{'atomic_habits_state.json':{content:JSON.stringify({state})}}});
 }});
 map.set('atomic_habit_cloud_config_v1',JSON.stringify({endpoint:'https://api.github.com/gists/fake',apiKey:'fake'}));
 vm.runInContext('cloudReference={habits:[],logs:{}}',ctx);
 assert.equal(await vm.runInContext('pushToCloud(true)',ctx),true);
 assert.equal(writes,0);
});
test('failed mobile write remains local and shows unverified status',async()=>{
 const old={habits:[{id:'fake'}],logs:{}};let method=[];
 const {ctx,elements,map}=setup({host:'phone.example',fetchImpl:async(url,opts)=>{method.push(opts.method); if(opts.method==='GET') return response({files:{'atomic_habits_state.json':{content:JSON.stringify({state:old})}}});return response({},false)}});
 map.set('atomic_habit_cloud_config_v1',JSON.stringify({endpoint:'https://api.github.com/gists/fake',apiKey:'fake'}));
 vm.runInContext('cloudReference={habits:[{id:"fake"}],logs:{}};appState.logs={today:{fake:true}}',ctx);
 assert.equal(await vm.runInContext('saveState()',ctx),false);assert.ok(map.has('atomic_habit_streak_os_v1'));assert.deepEqual(method,['GET','PATCH']);assert.match(elements['save-status'].textContent,/Lỗi/);
});
test('different browser and server versions are preserved and saving is blocked',async()=>{
 const server={habits:[{id:'server'}],logs:{}};let posts=0;
 const {ctx,elements,map}=setup({fetchImpl:async(url,opts)=>{if(opts?.method==='POST')posts++;return response({exists:true,state:server,cloud_status:'matches'})}});
 const prior=JSON.stringify({habits:[{id:'browser'}],logs:{}});map.set('atomic_habit_streak_os_v1',prior);
 vm.runInContext('renderAll=()=>{}',ctx);
 await vm.runInContext('loadState()',ctx);
 assert.equal(map.get('atomic_habit_streak_os_v1'),prior);assert.match(elements['save-status'].textContent,/khác nhau/);
 assert.equal(await vm.runInContext('saveState()',ctx),false);assert.equal(posts,0);assert.equal(map.get('atomic_habit_streak_os_v1'),prior);
});
test('an older cloud read cannot hide a newer save failure',async()=>{
 let resolveRead;
 const {ctx,elements}=setup({fetchImpl:(url,opts)=>opts?.method==='POST'
   ? Promise.resolve(response({local_saved:true,cloud_synced:false,cloud_error:'Gist unavailable'}))
   : new Promise(resolve=>{resolveRead=resolve})});
 const pending=vm.runInContext('pullFromCloud(true)',ctx);
 assert.equal(await vm.runInContext('saveState()',ctx),false);
 resolveRead(response({cloud_status:'matches'}));
 assert.equal(await pending,false);
 assert.match(elements['save-status'].textContent,/Gist unavailable/);
});
