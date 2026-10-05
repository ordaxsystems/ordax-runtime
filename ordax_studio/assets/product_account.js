(()=>{
  let mounted=false;
  let connectedEmail='';

  function setGlobalStatus(message){
    if(typeof window.setStatus==='function')window.setStatus(message);
    else{
      const node=document.getElementById('globalStatus');
      if(node)node.textContent=message||'';
    }
  }

  function createDialog(){
    const overlay=document.createElement('div');
    overlay.id='accountOverlay';
    overlay.className='accountOverlay hidden';
    overlay.innerHTML=`
      <section class="accountDialog" role="dialog" aria-modal="true" aria-labelledby="accountDialogTitle">
        <div class="accountDialogHead">
          <div class="brandMark">O</div>
          <div class="accountDialogTitle">
            <div class="eyebrow">CONEXÃO SEGURA</div>
            <h2 id="accountDialogTitle">Conectar conta ORDAX</h2>
            <p>Vincule este computador à sua conta ORDAX para acesso remoto autenticado ao Runtime. GitHub é uma integração separada e serve apenas como provedor de projetos/remotos; ele não substitui a conta ORDAX. Outros provedores de projeto seguem o mesmo limite.</p>
          </div>
          <button id="accountClose" class="accountClose" type="button" aria-label="Fechar">×</button>
        </div>
        <form id="accountForm" class="accountForm" autocomplete="on">
          <label>E-mail
            <input id="accountEmail" class="accountInput" type="email" autocomplete="username" maxlength="320" required>
          </label>
          <label>Senha
            <input id="accountPassword" class="accountInput" type="password" autocomplete="current-password" maxlength="2048" required>
          </label>
          <div class="accountActions">
            <button id="accountSubmit" class="primary" type="submit">Conectar computador</button>
            <button id="accountCancel" class="subtleButton" type="button">Cancelar</button>
          </div>
        </form>
        <div class="accountSecurity">A senha é enviada diretamente ao serviço de autenticação configurado por este aplicativo local. O Control Plane ORDAX não deve receber nem armazenar a senha. Credenciais de sessão usadas durante o vínculo não pertencem ao Studio.</div>
        <div class="accountSecurity accountAiNotice"><strong>IA e provedores:</strong> conectar sua Conta ORDAX não conecta automaticamente nenhum provedor de IA e, por si só, não consome cota de nenhum provedor. Cada conector é configurado separadamente e deve informar suas próprias regras de uso, cota e custo antes da ativação.</div>
        <div id="accountStatus" class="accountStatus" aria-live="polite"></div>
      </section>`;
    document.body.appendChild(overlay);
    return overlay;
  }

  function mount(){
    if(mounted)return;
    const appbar=document.querySelector('.appbar');
    const runtime=document.getElementById('runtimePill');
    if(!appbar||!runtime)return;
    mounted=true;

    const button=document.createElement('button');
    button.id='accountButton';
    button.type='button';
    button.className='accountButton';
    button.textContent='Conectar conta';
    button.title='Conectar este computador à conta ORDAX';
    appbar.insertBefore(button,runtime);

    const overlay=createDialog();
    const form=document.getElementById('accountForm');
    const email=document.getElementById('accountEmail');
    const password=document.getElementById('accountPassword');
    const submit=document.getElementById('accountSubmit');
    const status=document.getElementById('accountStatus');

    const close=()=>{
      overlay.classList.add('hidden');
      password.value='';
      status.textContent='';
      status.className='accountStatus';
    };
    const open=()=>{
      overlay.classList.remove('hidden');
      requestAnimationFrame(()=>email.focus());
    };

    button.onclick=open;
    document.getElementById('accountClose').onclick=close;
    document.getElementById('accountCancel').onclick=close;
    overlay.addEventListener('click',event=>{if(event.target===overlay)close()});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!overlay.classList.contains('hidden'))close()});

    form.addEventListener('submit',async event=>{
      event.preventDefault();
      const accountEmail=email.value.trim();
      const accountPassword=password.value;
      if(!accountEmail||!accountPassword)return;

      submit.disabled=true;
      status.className='accountStatus';
      status.innerHTML='<span class="accountSpinner"></span>Autenticando e vinculando este computador…';
      setGlobalStatus('Conectando conta ORDAX...');

      let result;
      try{
        const connect=window.ordaxStudioHost?.connectProductAccount;
        if(typeof connect!=='function'){
          result={ok:false,summary:'Esta edição do ORDAX Studio não expõe conexão de conta.'};
        }else{
          result=await connect(accountEmail,accountPassword);
        }
      }catch(error){
        result={ok:false,summary:String(error)};
      }finally{
        password.value='';
        submit.disabled=false;
      }

      if(!result?.ok){
        status.className='accountStatus error';
        status.textContent=result?.summary||'Não foi possível conectar a conta ORDAX.';
        setGlobalStatus('Conta ORDAX não conectada');
        return;
      }

      connectedEmail=result.data?.email||accountEmail;
      button.textContent='Conta conectada';
      button.classList.add('connected');
      button.title=connectedEmail?`Conectado como ${connectedEmail}`:'Conta ORDAX conectada';
      status.className='accountStatus success';
      const enrolledNow=Boolean(result.data?.enrolled_now);
      const restarted=Boolean(result.data?.runtime_restarted);
      const restartRequired=Boolean(result.data?.runtime_restart_required);
      if(enrolledNow&&restarted){
        status.textContent=connectedEmail?`Computador registrado e vinculado a ${connectedEmail}. Runtime reiniciado.`:'Computador registrado e vinculado à conta ORDAX. Runtime reiniciado.';
      }else if(enrolledNow&&restartRequired){
        status.textContent='Computador registrado. Feche e abra o ORDAX Studio ou reinicie o Runtime para concluir a conexão remota.';
      }else{
        status.textContent=connectedEmail?`Computador vinculado a ${connectedEmail}.`:'Computador vinculado à conta ORDAX.';
      }
      setGlobalStatus(restartRequired?'ORDAX registrado · Runtime precisa reiniciar':'ORDAX conectado à conta');
      setTimeout(close,restartRequired?2400:1100);
    });
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(mount,0),{once:true});
  else setTimeout(mount,0);
})();
