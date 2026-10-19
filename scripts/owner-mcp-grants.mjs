#!/usr/bin/env node
// Owner grant administration for OrdaX Runtime; independent of Studio UI.
// Only the canonical authenticated Owner API may create/revoke grants.
import {createHash,randomBytes,timingSafeEqual} from 'node:crypto';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const AUTH='https://jhfphsjptrpmtnzkpwud.supabase.co';
const API='https://mcp.ordax.com.br';
const CLIENT='202fd3e1-b058-4531-8750-82c57e4b6ca0'; // existing public first-party OAuth ID
const REDIRECT='http://127.0.0.1:8766/oauth/callback';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DEVICE=new Set(['full-computer-control','interactive-computer-control','computer-filesystem','computer-observe','computer-clipboard','computer-process-control','app-intelligence-read']);
const PROJECT=new Set(['project-browser-automation','project-continuity']);
const uuid=v=>typeof v==='string'&&UUID.test(v);
export function parseArgs(argv){
 const [command,...rest]=argv;
 if(!['list','authorize','revoke'].includes(command))throw Error('usage: <list|authorize|revoke> --device UUID [--client UUID] [--profile NAME] [--group UUID] [--days 1..365] [--space UUID --project UUID]');
 const a={command};
 for(let i=0;i<rest.length;i+=2){
   const k=rest[i]?.slice(2);
   if(!rest[i]?.startsWith('--')||!['device','client','profile','group','days','space','project'].includes(k)||rest[i+1]===undefined||k in a)throw Error('invalid option');
   a[k]=rest[i+1];
 }
 if(!uuid(a.device)||a.client&&!uuid(a.client))throw Error('invalid device/client UUID');
 if(command==='revoke'&&!uuid(a.group))throw Error('revoke requires --group UUID');
 if(command==='authorize'){
   if(!DEVICE.has(a.profile)&&!PROJECT.has(a.profile))throw Error('unsupported profile');
   if(PROJECT.has(a.profile)!==Boolean(a.space||a.project))throw Error('project scope/profile mismatch');
   if((a.space||a.project)&&(!uuid(a.space)||!uuid(a.project)))throw Error('both project and space UUID required');
   a.days=a.days===undefined?30:Number(a.days);
   if(!Number.isInteger(a.days)||a.days<1||a.days>365)throw Error('days outside 1..365');
 }
 return a;
}
export function selectClient(authority,client){
 const cs=Array.isArray(authority?.clients)?authority.clients.filter(c=>uuid(c?.client_id)):[];
 const matches=client?cs.filter(c=>c.client_id.toLowerCase()===client.toLowerCase()):cs;
 if(matches.length!==1)throw Error(matches.length?'specify --client (multiple OAuth clients)':'no consented MCP client');
 return matches[0].client_id;
}
const b64=v=>Buffer.from(v).toString('base64url');
const equal=(a,b)=>typeof a==='string'&&a.length===b.length&&timingSafeEqual(Buffer.from(a),Buffer.from(b));
function openBrowser(url){
 const exe=process.platform==='win32'?'explorer.exe':process.platform==='darwin'?'open':'xdg-open';
 const p=spawn(exe,[url],{detached:true,stdio:'ignore',windowsHide:true});
 p.on('error',()=>process.stderr.write('Open the authorization URL manually.\n'));p.unref();
}
async function login(){
 const verifier=b64(randomBytes(48)),state=b64(randomBytes(32));
 const challenge=b64(createHash('sha256').update(verifier).digest());
 let finish,fail,done=false;
 const answer=new Promise((yes,no)=>{finish=yes;fail=no});
 let timeoutHandle;
 const server=createServer((req,res)=>{
   const respond=(status,msg)=>{res.writeHead(status,{'content-type':'text/plain; charset=utf-8','cache-control':'no-store','content-security-policy':"default-src 'none'"});res.end(msg)};
   const u=new URL(req.url,REDIRECT);
   if(req.method!=='GET'||req.headers.host!=='127.0.0.1:8766'||u.pathname!=='/oauth/callback')return respond(404,'Not found');
   if(done)return respond(409,'Already received');
   if(!equal(u.searchParams.get('state'),state))return respond(403,'Invalid authorization state');
   done=true;
   const code=u.searchParams.get('code');
   if(u.searchParams.has('error')||!code||code.length>4096){respond(400,'Authorization rejected');fail(Error('oauth_rejected'))}
   else{respond(200,'OrdaX authorization received. You may close this tab.');finish(code)}
 });
 try{
   await new Promise((yes,no)=>{server.once('error',no);server.listen(8766,'127.0.0.1',yes)});
   const url=new URL(AUTH+'/auth/v1/oauth/authorize');
   for(const [k,v] of Object.entries({response_type:'code',client_id:CLIENT,redirect_uri:REDIRECT,scope:'openid email offline_access',state,code_challenge:challenge,code_challenge_method:'S256'}))url.searchParams.set(k,v);
   process.stdout.write('Authorize OrdaX Owner in your browser:\n'+url+'\n');
   openBrowser(String(url));
   const code=await Promise.race([answer,new Promise((_,no)=>{timeoutHandle=setTimeout(()=>no(Error('oauth_timeout')),300000)})]);
   const body=new URLSearchParams({grant_type:'authorization_code',client_id:CLIENT,redirect_uri:REDIRECT,code,code_verifier:verifier});
   const r=await fetch(AUTH+'/auth/v1/oauth/token',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body,signal:AbortSignal.timeout(20000)});
   const v=await r.json();
   if(!r.ok||typeof v?.access_token!=='string'||v.access_token.length<16||v.access_token.length>16000)throw Error('oauth_exchange_failed');
   return v.access_token; // never logged or persisted
 }finally{clearTimeout(timeoutHandle);server.close()}
}
async function api(token,method,path,payload){
 const r=await fetch(new URL(path,API),{method,headers:{authorization:'Bearer '+token,accept:'application/json',...(payload?{'content-type':'application/json'}:{})},body:payload?JSON.stringify(payload):undefined,redirect:'error',signal:AbortSignal.timeout(20000)});
 const v=await r.json();
 if(!r.ok||v?.ok!==true)throw Error('Owner API refused: '+(typeof v?.error==='string'?v.error:r.status));
 return v;
}
async function main(){
 const a=parseArgs(process.argv.slice(2)),token=await login();
 const authority=await api(token,'GET','/v3/owner/remote-authority?device_id='+encodeURIComponent(a.device));
 if(authority.device_id?.toLowerCase()!==a.device.toLowerCase())throw Error('owner device mismatch');
 if(a.command==='list'){process.stdout.write(JSON.stringify({clients:authority.clients,grant_groups:authority.grant_groups},null,2)+'\n');return}
 const client_id=selectClient(authority,a.client);
 if(a.command==='authorize'){
   const valid_until=new Date(Date.now()+a.days*86400000).toISOString();
   const result=await api(token,'POST','/v3/owner/remote-grant-groups',{client_id,device_id:a.device,profile_key:a.profile,space_id:a.space??null,project_id:a.project??null,valid_until});
   process.stdout.write(JSON.stringify({ok:true,result},null,2)+'\n');return;
 }
 const group=authority.grant_groups?.find(g=>g.grant_group_id?.toLowerCase()===a.group.toLowerCase()&&g.client_id?.toLowerCase()===client_id.toLowerCase());
 if(!group)throw Error('grant is not owned by this client/device');
 const result=await api(token,'DELETE','/v3/owner/remote-grant-groups/'+encodeURIComponent(a.group)+'?client_id='+encodeURIComponent(client_id));
 process.stdout.write(JSON.stringify({ok:true,result},null,2)+'\n');
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)
 main().catch(e=>{process.stderr.write('OrdaX Owner grant failed: '+e.message+'\n');process.exitCode=1});
