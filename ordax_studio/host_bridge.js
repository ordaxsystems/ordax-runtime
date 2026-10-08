(()=>{
  let nativeRpcSequence=0;
  const nativeRpcPending=new Map();
  const readyCallbacks=new Set();
  let ready=false;

  function markReady(){
    if(ready)return;
    ready=true;
    for(const callback of readyCallbacks){
      try{callback()}catch(error){queueMicrotask(()=>{throw error})}
    }
    readyCallbacks.clear();
  }

  function invokeWebView2(method,args){
    return new Promise((resolve,reject)=>{
      const id='rpc-'+(++nativeRpcSequence)+'-'+Date.now();
      nativeRpcPending.set(id,{resolve,reject});
      window.chrome.webview.postMessage({type:'ordax-rpc',id,method,args});
      setTimeout(()=>{
        const pending=nativeRpcPending.get(id);
        if(!pending)return;
        nativeRpcPending.delete(id);
        reject(new Error('ORDAX native bridge timeout'));
      },120000);
    });
  }

  async function invoke(method,args){
    const pyApi=window.pywebview?.api;
    if(pyApi&&typeof pyApi[method]==='function')return await pyApi[method](...args);
    if(window.chrome?.webview)return await invokeWebView2(method,args);
    throw new Error('ORDAX host bridge indisponível');
  }

  if(window.chrome?.webview){
    window.chrome.webview.addEventListener('message',event=>{
      const message=event.data||{};
      if(message.type==='ordax-assistant-surface-status'){
        // Native host owns navigation state. Never accept provider URLs, cookies or auth claims.
        if(['loading','ready','error','hidden'].includes(message.state)){
          window.dispatchEvent(new CustomEvent('ordax-assistant-surface-status',{
            detail:{state:message.state}
          }));
        }
        return;
      }
      const id=String(message.id||'');
      const pending=nativeRpcPending.get(id);
      if(!pending)return;
      nativeRpcPending.delete(id);
      if(message.error)pending.reject(new Error(String(message.error)));
      else pending.resolve(message.result);
    });
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',markReady,{once:true});
    else queueMicrotask(markReady);
  }

  window.addEventListener('pywebviewready',markReady,{once:true});
  if(window.pywebview?.api)queueMicrotask(markReady);

  const api=Object.freeze({
    projectsCatalog:(...args)=>invoke('projects_catalog',args),
    startupProject:(...args)=>invoke('startup_project',args),
    bootstrap:(...args)=>invoke('bootstrap',args),
    selectProject:(...args)=>invoke('select_project',args),
    inventory:(...args)=>invoke('inventory',args),
    readFile:(...args)=>invoke('read_file',args),
    saveFile:(...args)=>invoke('save_file',args),
    previewStatus:(...args)=>invoke('preview_status',args),
    previewStart:(...args)=>invoke('preview_start',args),
    previewStop:(...args)=>invoke('preview_stop',args),
    previewCapture:(...args)=>invoke('preview_capture',args),
    previewLogs:(...args)=>invoke('preview_logs',args),
    previewImage:(...args)=>invoke('preview_image',args),
    executionStatus:(...args)=>invoke('execution_status',args),
    taskAdd:(...args)=>invoke('task_add',args),
    checkpoint:(...args)=>invoke('checkpoint',args),
    productStatus:(...args)=>invoke('product_status',args),
    computerAccessSettings:(...args)=>invoke('computer_access_settings',args),
    saveComputerAccessSettings:(...args)=>invoke('save_computer_access_settings',args),
    health:(...args)=>invoke('health',args),
    briefing:(...args)=>invoke('briefing',args),
    search:(...args)=>invoke('search',args),
    gitDiff:(...args)=>invoke('git_diff',args),
    memoryContext:(...args)=>invoke('memory_context',args),
    aiSessionsStatus:(...args)=>invoke('ai_sessions_status',args),
    assistantCatalog:(...args)=>invoke('assistant_catalog',args),
    assistantState:(...args)=>invoke('assistant_state',args),
    assistantCreateChat:(...args)=>invoke('assistant_create_chat',args),
    assistantSelectChat:(...args)=>invoke('assistant_select_chat',args),
    assistantUpdateChat:(...args)=>invoke('assistant_update_chat',args),
    assistantCloseChat:(...args)=>invoke('assistant_close_chat',args),
    connectProductAccount:(...args)=>invoke('connect_product_account',args),
    remoteComputerGrants:(...args)=>invoke('remote_computer_grants',args),
    authorizeRemoteComputerGrant:(...args)=>invoke('authorize_remote_computer_grant',args),
    revokeRemoteComputerGrant:(...args)=>invoke('revoke_remote_computer_grant',args),
    remoteAppIntelligenceGrants:(...args)=>invoke('remote_app_intelligence_grants',args),
    authorizeRemoteAppIntelligenceGrant:(...args)=>invoke('authorize_remote_app_intelligence_grant',args),
    revokeRemoteAppIntelligenceGrant:(...args)=>invoke('revoke_remote_app_intelligence_grant',args),
    remoteProjectBrowserGrants:(...args)=>invoke('remote_project_browser_grants',args),
    authorizeRemoteProjectBrowserGrant:(...args)=>invoke('authorize_remote_project_browser_grant',args),
    revokeRemoteProjectBrowserGrant:(...args)=>invoke('revoke_remote_project_browser_grant',args),
    browserList:(...args)=>invoke('browser_list',args),
    browserStart:(...args)=>invoke('browser_start',args),
    browserStatus:(...args)=>invoke('browser_status',args),
    browserNavigate:(...args)=>invoke('browser_navigate',args),
    browserSnapshot:(...args)=>invoke('browser_snapshot',args),
    browserScreenshot:(...args)=>invoke('browser_screenshot',args),
    browserStop:(...args)=>invoke('browser_stop',args),
    blenderPrepare:(...args)=>invoke('blender_prepare',args),
    blenderInstallBridge:(...args)=>invoke('blender_install_bridge',args),
    blenderInstances:(...args)=>invoke('blender_instances',args),
    blenderAdopt:(...args)=>invoke('blender_adopt',args),
    blenderStart:(...args)=>invoke('blender_start',args),
    presentAssistantSurface(payload){
      if(window.chrome?.webview){
        window.chrome.webview.postMessage(payload);
        return true;
      }
      return false;
    },
    whenReady(callback){
      if(typeof callback!=='function')throw new TypeError('ORDAX host bridge ready callback must be a function');
      if(ready){queueMicrotask(callback);return}
      readyCallbacks.add(callback);
    },
  });

  Object.defineProperty(window,'ordaxStudioHost',{value:api,writable:false,configurable:false});
})();
