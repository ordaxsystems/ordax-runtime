function computerManaged(field){return Boolean(state.computerAccess?.managed_by_environment?.[field])}
function computerAccessValues(field){return [...(state.computerAccess?.[field]||[])]}
function managedNote(field){return computerManaged(field)?'<span class="managedBadge">Gerenciado pelo ambiente</span>':''}
async function loadComputerAccess(){
  const result=await call('computer_access_settings');
  state.computerAccess=result?.ok?(result.data||null):null;
  renderComputerAccessCanvas(result);
}
function removeComputerAccessValue(field,index){
  if(!state.computerAccess||computerManaged(field))return;
  const values=computerAccessValues(field);values.splice(index,1);state.computerAccess[field]=values;renderComputerAccessCanvas();
}
function addComputerAccessValue(field,inputId){
  if(!state.computerAccess||computerManaged(field))return;
  const input=$(inputId),value=String(input?.value||'').trim();if(!value)return;
  const values=computerAccessValues(field);if(!values.some(item=>String(item).toLowerCase()===value.toLowerCase()))values.push(value);
  state.computerAccess[field]=values;renderComputerAccessCanvas();
}
function renderComputerAccessCanvas(result=null){
  const root=$('computerCanvas'),access=state.computerAccess;
  if(!root)return;
  if(!access){root.innerHTML=`<div class="panelContent"><div class="infoCard"><h4>ACESSO AO COMPUTADOR</h4><div class="sideMeta">${escapeHtml(result?.summary||'Política local indisponível.')}</div></div></div>`;return}
  const roots=(access.allowed_roots||[]).map((value,index)=>`<div class="accessItem"><code>${escapeHtml(value)}</code>${computerManaged('allowed_roots')?'':`<button data-remove-root="${index}" title="Remover pasta">×</button>`}</div>`).join('')||'<div class="sideMeta">Nenhuma pasta autorizada.</div>';
  const apps=(access.allowed_applications||[]).map((value,index)=>`<div class="accessItem"><code>${escapeHtml(value)}</code>${computerManaged('allowed_applications')?'':`<button data-remove-app="${index}" title="Remover aplicativo">×</button>`}</div>`).join('')||'<div class="sideMeta">Nenhum aplicativo autorizado no modo limitado.</div>';
  const fullAccess=Boolean(access.full_access);
  root.innerHTML=`<div class="panelContent accessPanel"><section class="agentWelcome"><div class="agentWelcomeTop"><span class="orb"></span><div><div class="eyebrow">POLÍTICA LOCAL DO DONO</div><h2>Acesso ao computador</h2></div></div><p>Esta política define o limite máximo do Runtime neste PC. O dono pode usar o modo limitado ou autorizar Full Access para clientes ORDAX remotos já autenticados.</p></section><div class="infoCard"><h4>CONTROLE PRINCIPAL</h4><label class="accessToggle"><span><strong>Permitir Computer Control</strong><small>Desative para bloquear o controle do computador pelo Runtime.</small></span><input id="computerEnabled" type="checkbox" ${access.enabled?'checked':''} ${computerManaged('enabled')?'disabled':''}></label>${managedNote('enabled')}<label class="accessToggle accessDanger"><span><strong>Full Access</strong><small>Remove as allowlists ORDAX de pastas e aplicativos para todas as capacidades Computer Control suportadas. O acesso continua limitado apenas pela autorização remota e pelas permissões reais do Windows/UAC.</small></span><input id="computerFullAccess" type="checkbox" ${fullAccess?'checked':''} ${computerManaged('full_access')?'disabled':''}></label>${managedNote('full_access')}<div class="accessWarning"><strong>Acesso amplo ao computador</strong><span>Com Full Access ativo, um cliente ORDAX remoto autorizado pode ler e alterar arquivos, usar mouse/teclado/clipboard, inspecionar janelas e processos e iniciar aplicativos suportados sem uma allowlist por pasta ou aplicativo. O cliente remoto não pode ativar este modo por conta própria.</span></div><label class="accessToggle"><span><strong>Filesystem completo, mantendo allowlist de apps</strong><small>Opção intermediária: remove apenas a restrição por pastas; aplicativos continuam limitados pela lista abaixo.</small></span><input id="computerFullFilesystem" type="checkbox" ${access.full_filesystem?'checked':''} ${computerManaged('full_filesystem')?'disabled':''}></label>${managedNote('full_filesystem')}</div><div class="infoCard"><div class="accessHeading"><div><h4>PASTAS PERMITIDAS — MODO LIMITADO</h4><div class="sideMeta">Até 32 raízes absolutas. Esta lista é ignorada enquanto Full Access estiver ativo.</div></div>${managedNote('allowed_roots')}</div><div class="accessList">${roots}</div><div class="accessComposer"><input id="computerRootInput" placeholder="C:\\Users\\SeuUsuario\\Documents" ${computerManaged('allowed_roots')?'disabled':''}><button id="computerRootAdd" ${computerManaged('allowed_roots')?'disabled':''}>Adicionar pasta</button></div></div><div class="infoCard"><div class="accessHeading"><div><h4>APLICATIVOS PERMITIDOS — MODO LIMITADO</h4><div class="sideMeta">Até 64 executáveis. Esta lista é ignorada enquanto Full Access estiver ativo.</div></div>${managedNote('allowed_applications')}</div><div class="accessList">${apps}</div><div class="accessComposer"><input id="computerAppInput" placeholder="notepad.exe ou C:\\Program Files\\App\\app.exe" ${computerManaged('allowed_applications')?'disabled':''}><button id="computerAppAdd" ${computerManaged('allowed_applications')?'disabled':''}>Adicionar aplicativo</button></div></div><div class="accessFooter"><div class="sideMeta">Arquivo: ${escapeHtml(access.settings_path||'agent-settings.json')}<br>Revisão ${escapeHtml(String(access.revision||'').slice(0,12))}</div><button id="computerAccessSave" class="primary">Salvar política local</button></div></div>`;
  root.querySelectorAll('[data-remove-root]').forEach(button=>button.onclick=()=>removeComputerAccessValue('allowed_roots',Number(button.dataset.removeRoot)));
  root.querySelectorAll('[data-remove-app]').forEach(button=>button.onclick=()=>removeComputerAccessValue('allowed_applications',Number(button.dataset.removeApp)));
  $('computerRootAdd').onclick=()=>addComputerAccessValue('allowed_roots','computerRootInput');
  $('computerAppAdd').onclick=()=>addComputerAccessValue('allowed_applications','computerAppInput');
  $('computerAccessSave').onclick=saveComputerAccess;
}
async function saveComputerAccess(){
  const access=state.computerAccess;if(!access)return;
  const enabled=computerManaged('enabled')?access.enabled:Boolean($('computerEnabled')?.checked),fullAccess=computerManaged('full_access')?access.full_access:Boolean($('computerFullAccess')?.checked),full=computerManaged('full_filesystem')?access.full_filesystem:Boolean($('computerFullFilesystem')?.checked);
  if(fullAccess&&!access.full_access&&!window.confirm('Autorizar Full Access neste computador? Clientes ORDAX remotos já autorizados poderão usar todas as capacidades Computer Control suportadas sem allowlists de pastas ou aplicativos. O Windows/UAC continuará sendo a fronteira final.')){$('computerFullAccess').checked=false;return}
  if(enabled&&!fullAccess&&!full&&!computerAccessValues('allowed_roots').length){setStatus('Adicione pelo menos uma pasta permitida, ative Full Access ou desative o Computer Control.');return}
  setStatus('Salvando política local...');
  const result=await call('save_computer_access_settings',{enabled,full_access:fullAccess,full_filesystem:full,allowed_roots:computerAccessValues('allowed_roots'),allowed_applications:computerAccessValues('allowed_applications'),expected_revision:access.revision});
  if(!result?.ok){setStatus(result?.summary||'Falha ao salvar política local');await loadComputerAccess();return}
  state.computerAccess=result.data||null;renderComputerAccessCanvas(result);setStatus('Política local atualizada');setTimeout(()=>setStatus('Pronto'),1200);
}
