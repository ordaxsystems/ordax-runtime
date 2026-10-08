import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source=readFileSync(new URL('../ordax_studio/host_bridge.js',import.meta.url),'utf8');

function fixture(){
  const events=new Map();
  const dispatched=[];
  const webview={
    postMessage(){},
    addEventListener(name,callback){events.set(name,callback)}
  };
  const window={
    chrome:{webview},
    addEventListener(){},
    dispatchEvent(event){dispatched.push({type:event.type,detail:event.detail})}
  };
  const document={readyState:'loading',addEventListener(){}};
  class CustomEvent {
    constructor(type,options){this.type=type;this.detail=options.detail}
  }
  runInNewContext(source,{window,document,CustomEvent,queueMicrotask(){},setTimeout(){}});
  assert.equal(typeof window.ordaxStudioHost.presentAssistantSurface,'function');
  return {events,dispatched,window};
}

test('native provider status is forwarded through the existing host bridge',()=>{
  const f=fixture();
  for(const state of ['loading','ready','error','unavailable','hidden']){
    f.events.get('message')({data:{type:'ordax-assistant-surface-status',state}});
  }
  assert.deepEqual(f.dispatched.map(e=>e.detail.state),['loading','ready','error','unavailable','hidden']);
  assert.ok(f.dispatched.every(e=>e.type==='ordax-assistant-surface-status'));
});

test('unknown states, URL, tokens and arbitrary messages cannot be reflected to the Studio UI',()=>{
  const f=fixture();
  f.events.get('message')({data:{type:'ordax-assistant-surface-status',state:'authenticated',access_token:'nope'}});
  f.events.get('message')({data:{type:'ordax-assistant-surface-status',state:'ready',url:'https://other.example',cookie:'secret'}});
  f.events.get('message')({data:{type:'unknown',state:'ready'}});
  assert.equal(f.dispatched.length,1);
  assert.deepEqual(Object.keys(f.dispatched[0].detail),['state']);
  assert.equal(f.dispatched[0].detail.state,'ready');
});

test('assistant status never replaces or triggers an RPC method',()=>{
  const f=fixture();
  f.events.get('message')({data:{type:'ordax-assistant-surface-status',state:'loading',id:'rpc-1-123',result:'fake'}});
  assert.equal(f.dispatched.length,1);
  assert.equal(f.dispatched[0].detail.state,'loading');
});

test('WebView2 init failures are surfaced as bounded unavailable status, not credentials',()=>{
  const f=fixture();
  f.events.get('message')({data:{type:'ordax-assistant-surface-status',state:'unavailable',access_token:'private',url:'https://secret.invalid'}});
  assert.equal(f.dispatched.length,1);
  assert.equal(f.dispatched[0].detail.state,'unavailable');
  assert.deepEqual(Object.keys(f.dispatched[0].detail),['state']);
});
