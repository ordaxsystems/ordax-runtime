(()=>{
  const CARD_ID='blenderConnectionCard';
  let busy=false;
  let refreshTimer=null;

  const el=id=>document.getElementById(id);
  const activeProject=()=>typeof state==='object'&&state?state.project:null;
  const hostMethods=Object.freeze({preview_capture:'previewCapture',blender_install_bridge:'blenderInstallBridge',blender_adopt:'blenderAdopt',blender_start:'blenderStart',blender_prepare:'blenderPrepare'});
  const api=async(name,...args)=>{
    const methodName=hostMethods[name];
    const fn=methodName&&window.ordaxStudioHost?.[methodName];
    if(typeof fn!=='function')return{ok:false,summary:`API indisponível: ${name}`};
    try{return await fn(...args)}catch(error){return{ok:false,summary:String(error)}}
  };
  const blenderMode=()=>/^blender\b/i.test((el('previewMeta')?.textContent||'').trim());

  function ensureCard(){
    let card=el(CARD_ID);
    if(card)return card;
    const pane=document.querySelector('.previewPane');
    const toolbar=pane?.querySelector('.previewToolbar');
    if(!pane||!toolbar)return null;
    card=document.createElement('section');
    card.id=CARD_ID;
    card.className='blenderConnectionCard hidden';
    card.setAttribute('aria-live','polite');
    toolbar.insertAdjacentElement('afterend',card);
    return card;
  }

  function button(label,action,{primary=false,pid=null,allowBlank=false}={}){
    const item=document.createElement('button');
    item.type='button';
    item.textContent=label;
    item.dataset.action=action;
    if(pid!==null)item.dataset.pid=String(pid);
    if(allowBlank)item.dataset.allowBlank='true';
    if(primary)item.classList.add('primary');
    return item;
  }

  function render(connection,summary=''){
    const card=ensureCard();
    if(!card)return;
    if(!blenderMode()||!connection||connection.state==='not_blender'){
      card.classList.add('hidden');
      card.replaceChildren();
      return;
    }
    card.classList.remove('hidden');
    card.replaceChildren();

    const text=document.createElement('div');
    text.className='blenderConnectionText';
    const title=document.createElement('strong');
    const detail=document.createElement('span');
    const actions=document.createElement('div');
    actions.className='blenderConnectionActions';
    const pid=connection.pid?`PID ${connection.pid}`:'';

    if(['connected','adopted','adopted_blank'].includes(connection.state)){
      title.textContent=connection.state==='connected'?'Blender conectado':'Blender adotado';
      detail.textContent=[pid,connection.file].filter(Boolean).join(' · ')||summary||'Companion ORDAX ativo.';
      actions.append(button('Capturar','capture',{primary:true}),button('Reverificar','refresh'));
    }else if(connection.state==='identity_mismatch'){
      title.textContent='Sessão Blender fora do projeto';
      detail.textContent=summary||'A sessão ORDAX está ativa, mas a identidade ou o arquivo aberto não corresponde a este projeto.';
      actions.append(button('Reverificar','refresh',{primary:true}));
    }else if(connection.state==='restart_required'){
      title.textContent='Bridge ORDAX pendente';
      detail.textContent=summary||'Instale/atualize o bridge e reabra somente a janela Blender indicada uma vez.';
      actions.append(button('Instalar bridge','install',{primary:true}),button('Reverificar','refresh'));
    }else if(connection.state==='ambiguous'||connection.state==='blank_ambiguous'){
      title.textContent='Escolha a janela Blender';
      detail.textContent=connection.state==='blank_ambiguous'?'Há mais de uma janela vazia pronta para adoção.':'Há mais de uma janela compatível com este projeto.';
      const instances=connection.instances||[];
      for(const instance of instances){
        const name=instance.file?String(instance.file).split(/[\\/]/).pop():'janela vazia';
        const label=`Adotar PID ${instance.pid} · ${name}`;
        actions.append(button(label,'adopt',{pid:instance.pid,allowBlank:connection.state==='blank_ambiguous',primary:true}));
      }
      actions.append(button('Reverificar','refresh'));
    }else if(connection.state==='occupied'){
      title.textContent='Blender em uso por outro projeto';
      detail.textContent=summary||'Abra uma nova sessão para este projeto ou libere uma das janelas atuais.';
      actions.append(button('Abrir nova sessão','start',{primary:true}),button('Reverificar','refresh'));
    }else if(connection.state==='error'){
      title.textContent='Conexão Blender indisponível';
      detail.textContent=summary||'Falha ao consultar o bridge ORDAX.';
      actions.append(button('Reverificar','refresh',{primary:true}));
    }else{
      title.textContent='Blender ainda não está aberto';
      detail.textContent='O ORDAX pode abrir uma sessão conectada para este projeto.';
      if(connection.can_start)actions.append(button('Abrir Blender','start',{primary:true}));
      actions.append(button('Reverificar','refresh'));
    }

    text.append(title,detail);
    card.append(text,actions);
    card.querySelectorAll('[data-action]').forEach(item=>item.addEventListener('click',onAction));
  }

  function renderApiResult(result,selector=value=>value){
    if(!result?.ok){
      render({state:'error'},result?.summary||'Falha na conexão Blender.');
      return false;
    }
    render(selector(result.data||{}),result.summary);
    return true;
  }

  async function onAction(event){
    if(busy)return;
    const projectAtStart=activeProject();
    const action=event.currentTarget.dataset.action;
    busy=true;
    event.currentTarget.disabled=true;
    try{
      if(action==='capture'){
        if(typeof window.capturePreview==='function')await window.capturePreview();
        else{
          const result=await api('preview_capture');
          if(activeProject()===projectAtStart&&!result?.ok)render({state:'error'},result?.summary||'Falha ao capturar o preview Blender.');
        }
      }else if(action==='install'){
        const result=await api('blender_install_bridge');
        if(activeProject()===projectAtStart)renderApiResult(result,data=>data.connection||{state:'error'});
      }else if(action==='adopt'){
        const result=await api('blender_adopt',Number(event.currentTarget.dataset.pid),event.currentTarget.dataset.allowBlank==='true');
        if(activeProject()!==projectAtStart)return;
        if(renderApiResult(result)&&typeof window.refreshPreview==='function')await window.refreshPreview(true);
      }else if(action==='start'){
        const result=await api('blender_start');
        if(activeProject()!==projectAtStart)return;
        if(renderApiResult(result)&&typeof window.refreshPreview==='function')await window.refreshPreview(true);
      }else{
        await refresh();
      }
    }finally{
      busy=false;
      schedule(80);
    }
  }

  async function refresh(){
    if(busy||!blenderMode()){
      if(!blenderMode())render(null);
      return;
    }
    const projectAtStart=activeProject();
    busy=true;
    let stale=false;
    try{
      const result=await api('blender_prepare');
      stale=activeProject()!==projectAtStart;
      if(!stale)renderApiResult(result);
    }finally{
      busy=false;
      if(stale)schedule(0);
    }
  }

  function schedule(delay=120){
    clearTimeout(refreshTimer);
    refreshTimer=setTimeout(refresh,delay);
  }

  function start(){
    ensureCard();
    const meta=el('previewMeta');
    const workspace=el('workspace');
    if(meta)new MutationObserver(()=>schedule()).observe(meta,{childList:true,subtree:true,characterData:true});
    if(workspace)new MutationObserver(()=>schedule()).observe(workspace,{attributes:true,attributeFilter:['class']});
    schedule(200);
  }

  window.refreshBlenderConnectionControls=refresh;
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
})();