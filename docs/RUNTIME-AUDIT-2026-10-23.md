# Runtime audit and operational readiness — 2026-10-23

## Scope and provenance

- Canonical repository verified before edits: `C:\Users\TONECOS\Documents\github\ordax-runtime`.
- `origin`: `https://github.com/ordaxsystems/ordax-runtime.git`.
- Initial audited base: `main` at `9154cb977bbad9f007e7734ebfbeeb4997e7db57` (PR #224). This follow-up reconciles the existing PR #175 with `main` at `db43388cf7d6c36cfaee5439c93a357f12b1929b`, after PRs #225 and #159 were integrated.
- The #175 follow-up was prepared in an isolated Runtime checkout. The `ordax-apps`, `ordax-os`, and `ordax-platform` repositories were not modified; status after subsequent merges is recorded below.
- At the time of the original audit, PR #159 was merged while #205/#208, #114/#121/#136 were open. Their current status is recorded below.

## Repository and PR state after follow-up — 2026-10-23

At PR #226 merge, Runtime `main` advanced from `ea8c863736ef4482c216235a994aa4b01e0e8322` to `95b48c91283c605cd8950e238d0d7542e4e479fc`.

- PR #175 merged at `b2049e72811ebb81d3e56f5341dd51e77c954206`. Its Linux recovery CI, Runtime Contracts, and Windows Product Build passed; no physical OrdaX OS execution is claimed.
- PR #114 merged at `c26af984970a5db8bae37444273c3fd2505d58c3`. It exposes the Product origin validated during Electron account authorization. Windows Product Build, Install Smoke, Packaging Contracts, Product MCP Minimum SDK and Runtime Contracts passed on its head. The pin remains canonical Apps Studio 0.15.9; no Apps source was changed.
- PR #136 merged at `ea8c863736ef4482c216235a994aa4b01e0e8322`. It updates canonical repository ownership and scope terminology, with a documentation regression test. The two focused tests passed locally; required Runtime, Product MCP, Native Composition, Packaging, Windows Product Build and Install Smoke checks passed in CI.
- PR #121 was closed as duplicate product-level documentation; its canonical source belongs to Apps.
- Runtime #205 remains a draft aligned with Platform #274, which is still draft and SQL proposal-only. Runtime #208 was closed without merge because its separate staged endpoints are absent from #274; its branch remains preserved for reference. No migration, deployment, grant issuance, authenticated Owner host composition or Apps capability is enabled.
- CI results above validate source/build/install-smoke workflows only. They do not prove a production MCP action, physical Windows recovery, or execution under an OrdaX OS supervisor.

## Follow-up after PR #226 — review snapshot

PR #226 merged the audit follow-up at `95b48c91283c605cd8950e238d0d7542e4e479fc`. At the start of this review, that was the remote `main` SHA. The status paragraph above now records the merge commit rather than its pre-merge parent.

### Runtime PR #205

- PR #205 is the only open Runtime PR. It is still draft at head `01f8bcde2d38266fc3f7cfdad5da8f2532e6fcc6`, based on `f6b4c38f1dcb356714a32c54b9a02fc17661751b`; GitHub reports `mergeable=false`.
- Static review of its changed resolver, HTTP client, and selection broker found no route to workspace reading without interactive Owner confirmation and a fresh exact-scope read-only proof. This is a source review only; the host composition and Apps port are absent.
- Existing checks on that old head passed Runtime Contracts, Packaging Contracts, Native Runtime Composition, Product MCP Minimum SDK, Windows Product Build, and Install Smoke. Signing, signed-release smoke, release governance, and Owner/Dev attestation were skipped. These checks do not validate a rebased branch or live Platform/Windows integration.
- Platform #274 remains draft and its SQL is proposal-only. Do not enable the resource lifecycle or merge #205 until the external contract, authenticated Owner/Device host composition, Apps integration, and required post-reconciliation checks are complete.

### Open issue triage at this snapshot

| Issue | Current evidence and next owner gate |
| --- | --- |
| #184 | The typed workspace-resource lifecycle remains pending. Runtime #205 is source-only; Platform #274 and the Apps host integration are not enabled. |
| #149 | Latest Owner/Dev evidence keeps `rollbackReady=false` and `applyAllowed=false`; the installed 0.15.9 bytes have no proven byte-exact rollback. No machine update was performed. |
| #127 | The recorded real-device result is a fail-closed `product_grant_not_resolved` denial, not a positive interaction E2E. Runtime `full_access` does not imply a remote grant; the grant/profile and target-discovery contract requires its canonical authority owner. |
| #49 | Production Authenticode signing is not available in the latest recorded gate; no approved signing service/runner is in place. Keep production publication closed until signing custody and final-byte verification exist. |
| #61 | The current Windows build script still embeds `https://mcp.ordax.com.br`. The issue requires a versioned Control Plane release reference; do not substitute a URL without that owner's canonical provenance contract. |
| #67 | The issue's source evidence is from 2026-10-09, while the Runtime lock now pins Apps Studio 0.15.9. Refresh the Apps source/composition evidence before changing the lock or claiming the Conversation surface is integrated. |
| #7 | Bounded action-result compatibility still depends on the OS-owned Studio Runtime v3 contract; no duplicate result service belongs in Runtime. |
| #1 | The bootstrap/migration issue remains open. Revalidate current consumers and legacy references before closing it or deleting legacy code; no deletion is justified by the issue title alone. |

No changes were made to `ordax-apps`, `ordax-os`, or `ordax-platform`. The issue list is a Runtime handoff snapshot, not evidence that cross-repository or physical gates passed.


### Follow-up — 2026-10-24

Runtime issue #1 was closed as completed after checking the current migration state:

- Runtime README names `ordaxsystems/ordax-runtime` as canonical and records the migration commit.
- The old `washingtonmsdj/mcp-blender` main tree contains only its retirement README; the operational source was removed in its retirement commit.
- The Runtime setup retains a bounded migration for existing managed checkouts, including dirty/busy guards and rollback. This compatibility path remains intentional.
- Windows Build and Install Smoke passed in CI on the source represented by PR #227. This does not establish installation or execution on a physical Owner machine.

The legacy repository was not deleted or archived. No Runtime branch cleanup was performed in this follow-up; #205 remains the only open Runtime PR, and the closed-unmerged #208 branch remains available as evidence for the rejected endpoint proposal.


## Findings

### Corrected here: operational status could be stale during a long action

The Runtime's normal dispatch loop already marks transport errors as
`reconnecting`. The separate keepalive thread used while an action is running
renewed the lease and emitted the 20-second device heartbeat, but a failed
heartbeat only wrote to stderr. The local `/status` snapshot could therefore
continue to say `connected` while the remote transport was failing. The
keepalive now records the transport error and a subsequent successful
heartbeat records recovery, without replacing the active `busy` job state.

The `/status` runtime object now reports `action_registry_ready`,
`product_dispatch_ready`, and `product_readiness_code`. Product dispatch
readiness requires a loaded ActionRegistry, configured remote control, paired
device identity, `ready` or `busy` lifecycle state, connected transport, and a
Product heartbeat no older than 60 seconds. Status consumers must continue to
treat `/health` as liveness only. The Studio status adapter exposes the new
readiness fields additively while retaining legacy `device_agent.ok` semantics.

This is diagnostic state only. It creates no grant and does not prove the
authorization, execution, completion, or audit receipt for any particular
action.

### Existing MCP path and its evidence boundary

The Product MCP adapter (`product_mcp_server.py`) delegates to the shared
typed handlers. A call submits once, waits using the returned request ID, and
returns a receipt; uncertain completion directs the caller to
`product_action_status` rather than replaying the action. The Runtime dispatcher
uses the existing Product transport and ActionRegistry. The Platform remains
the owner of remote authentication, grants, queueing, and audit; local Runtime
policy remains a separate execution check.

The code and contract tests establish source-level flow and recovery behavior,
not a live MCP session. No production MCP request or authorized computer action
was issued during this audit. Remote endpoint `/health` success is not evidence
that a particular device Runtime is paired, dispatch-ready, granted, or able
to complete an action.

### Windows and resource use

The Windows Product Runtime dispatch loop has one idle-claim path, with an
adaptive delay that settles between 30 and 60 seconds; it keeps the existing
20-second heartbeat. The optional wake listener is disabled by default and,
when enabled, bounds reconciliation claims to five minutes. The per-action
keepalive is scoped to the active action. Existing idle-budget tests cover the
query ceiling. No resident process or new polling loop was added to the Windows
path. The separate Linux recovery cadence is recorded below.

Windows silent-install restart and recovery changes merged through PR #159 at
`db43388cf7d6c36cfaee5439c93a357f12b1929b`; the Windows Product Build includes
install/upgrade smoke coverage. This audit did not execute an installer,
upgrade, rollback, or physical recovery on a user device. Process memory/CPU
snapshots and a loopback port probe are observations of this host only, not a
repeatable product smoke test.

### OrdaX OS Native boundary

`docs/NATIVE-BUILDROOT-COMPATIBILITY.md` records a real gap: the Runtime lock
targets CPython 3.12 while the current independent Base uses CPython 3.14;
several package versions differ and the MCP SDK has no Buildroot package. CI's
Buildroot artifact probe (PR #224) is a useful artifact/source check, but does
not prove that the complete agent dependency closure is packaged, installed,
or executed on an OrdaX OS device. The OS-owned private identity provider,
local Connector policy, single presence owner, and signed slot lifecycle remain
the existing contracts; this PR changes none of them.

### Owner Linux recovery transport

Merged PR #175 removed the Owner Linux agent's D1/WebSocket execution
path and its Gorilla WebSocket dependency. It uses only the existing
PostgreSQL-backed `/v3/device/transport/*` endpoints and rejects the obsolete
`ORDAX_OWNER_TRANSPORT` selector. Its fixed capability set remains
`agent.owner.snapshot`, `agent.owner.git_status`, `agent.owner.pull`,
`agent.owner.runtime_stop`, and `agent.owner.runtime_start`; it does not add a
shell, grant, queue, authority, or OS service. Payload digest checks, execution
context fencing, the local effect journal, and the terminal report outbox stay
in place.

An idle cycle performs a heartbeat and a claim. The follow-up starts at 12
seconds and grows to a 30-second cap after repeated empty claims, resetting
after a claimed job. At the cap that is about 5,760 transport requests per idle
device per day; this is a calculated budget, not observed Platform database
load. Linux CI passed on the revised PR #175 head before merge. Its static artifact
is built for Linux/amd64; neither installation under the real OrdaX OS
supervisor nor execution on an OS device is claimed. The OS owner must
still confirm the actual target architecture, credential-provider handoff,
single presence ownership, packaging, and lifecycle before pinning a release.

## Cross-repository handoffs

- **ordax-apps:** consume `device_agent.product_dispatch_ready` and
  `device_agent.product_readiness_code` (or the canonical `/status` fields)
  separately from local `/health` and `remote_mcp.ok`. An unknown transport
  state must remain unknown; endpoint presence alone must not render the local
  Runtime as operational. Any polling-frequency or UI changes stay in Apps.
- **ordax-os:** before device enablement, resolve the recorded CPython/dependency
  closure against the exact OS Buildroot artifact and demonstrate install,
  supervised lifecycle, heartbeat ownership, and execution of the signed
  Runtime on the OS target. No OS source was modified here.
- **ordax-platform:** keep OAuth/session/grant resolution, request queue, and
  audit receipt canonical. Runtime readiness is device telemetry and cannot
  replace those checks.

## Validation record

- Local source tests passed: `tests.test_runtime_operational_status`,
  `tests.test_product_idle_poll_budget`, and
  `tests.test_status_server_loopback_security` (15 tests total).
- Python AST parsing passed for the modified Runtime modules and tests;
  `git diff --check` passed.
- `tests.test_ordax_studio_web` was not run locally because the bundled Python
  environment does not include the Runtime's `httpx`/MCP dependencies. Its
  regression is included for the repository's full Runtime Contracts workflow.
- PR #159's merged revision passed its Windows install/upgrade CI smoke. PR #175
  merged after Linux and Runtime Contract checks passed on its revised head. The
  local Windows host did not complete a Linux Go cross-build, so no local Go
  build result is claimed.
- GitHub CI and physical Windows/OrdaX OS/MCP integration are separate gates;
  none is represented as passed by these local checks.

A passing source test or CI workflow is not a claim that the Windows installer
or an OrdaX OS device was exercised.

## Follow-up after PR #234 — 2026-10-23 (America/Sao_Paulo)

### Repository and validation snapshot

- Canonical repository: `ordaxsystems/ordax-runtime`; audited `main` at `749b688aa4c8be21ba471237135e6cffc699127b`. GitHub reported no open Runtime pull requests at this snapshot.
- The desktop's current working directory is the parent `ordax-apps` workspace and is not itself a Git checkout (`git rev-parse --show-toplevel` failed). A separate checkout exists at `ordax-apps/_runtime-audit-work`; its `origin` is `https://github.com/ordaxsystems/ordax-runtime.git`, confirming it is an independent canonical Runtime clone. Its local HEAD and cached `origin/main` were still at `9154cb977bbad9f007e7734ebfbeeb4997e7db57`, stale relative to the canonical remote snapshot above, and it contains preexisting tracked and untracked Runtime changes. This continuation left that worktree untouched and used the canonical repository through branch/PR. No Apps repository files were changed.
- The current main commit has 17 completed checks: 11 succeeded and 6 were skipped. Runtime Contracts, Windows installer/build/install-smoke, Native composition, Native Buildroot source/offline probes, and candidate checks succeeded. Signing/publication/release-governance skips are not release evidence.
- CI validates source and disposable workflow environments. It does not prove a physical user-device install, a live authorized MCP action, or execution under the real OrdaX OS supervisor.

### MCP and idle-resource follow-up

- Runtime PR #232 corrected the canonical Product MCP ingress default; its change is present in main.
- Runtime PR #233 reuses one bounded HTTP transport pool for Product MCP calls while keeping a fresh client/cookie jar per invocation. The architecture document records the resource limits and isolation boundary.
- Runtime PR #234 adds bounded exponential backoff to nonterminal Product action status reads while retaining an immediate first read and the existing deadline/recovery behavior. Its regression demonstrates six reads over an 8-second wait instead of sixteen at a fixed 500 ms cadence.
- These changes reduce connection churn and unnecessary status requests; they do not change grants, dispatch authority, or the meaning of online presence.

### Project-First M1 remains source-only

Runtime PR #229 added strict Product HTTP methods for Owner approval and enrolled Device confirmation using Platform's existing default-off routes. They require explicit positive stage receipts and keep tokens and filesystem paths out of JSON. The native Windows picker, project binding, live Device proof, and issuance client are still not composed into an authenticated packaged Owner/Device host.

Platform PRs #276/#277 provide proposal/source contracts only: the SQL remains unapplied, both `WORKSPACE_RESOURCE_VERIFIER_ENABLED` and `WORKSPACE_RESOURCE_ISSUANCE_ENABLED` remain disabled, and no live grant or deployment is claimed. Keep the Apps consumer at `capability_unavailable`; do not construct approval from MCP/model input or infer a grant from device `full_access`. Runtime issue #184 stays open for trusted host composition and the real disposable-fixture E2E with mid-scan revocation.

### Remaining open Runtime work and handoffs

The open Runtime issues at this checkpoint are #7, #49, #61, #67, #127, #149, and #184. Their gates remain distinct:

- #7 needs the Runtime/Platform action result contract to preserve the original Studio Runtime v3 request binding; Runtime must not synthesize missing actor/Space/project/device/client evidence.
- #49 needs an approved production signing identity and protected signing service; CI signing skips are expected until that release authority exists.
- #61 needs the Platform owner to establish and version the canonical endpoint/provenance reference before Runtime can enforce a cutover.
- #67 needs the Apps owner to provide the canonical conversation UI/host contract for the Windows product; Runtime must not fork that UI.
- #127 needs the Platform grants and Apps consent/discovery surfaces to align with local policy; Runtime must not invent remote interaction grants.
- #149 needs authorized Windows recovery/rollback evidence on the real target installation; current install-smoke does not establish byte-exact recovery of a pre-attestation device.
- #184 needs trusted interactive host composition plus Platform's governed migration/opt-in and Apps integration before the read-only workspace flow can be called operational.

No source-only pass above closes these integration, release-authority, deployment, or physical-device gates.

### Local worktree snapshot and reconciliation — 2026-10-24

Before reconciling the pre-existing dirty local Runtime checkout, its full tracked and untracked state was preserved verbatim on `preserve/runtime-audit-worktree-20261024` at `b6f01f39af829010d72e17ce053cbcb0c8819f35`, parent `9154cb977bbad9f007e7734ebfbeeb4997e7db57`. All six captured blobs match the checkout's raw Git object hashes. The local worktree was left untouched. This branch is a recovery snapshot, not a merge candidate.

The snapshot's draft Product readiness implementation duplicates functionality now on `main`. The canonical `product_dispatch_readiness` additionally requires a finite Product heartbeat no older than 60 seconds. Existing operational-status tests cover missing/stale heartbeats and keepalive failure while a job remains active; Studio tests cover remote endpoint presence separately from device readiness. Replaying the old implementation would duplicate the contract and omit the heartbeat-freshness gate, so no code PR was opened from that snapshot.

GitHub API verified `main` at `af5b3a306aba0381babd4f608d3accde98f3dd64`. The local fetch could not reach `github.com:443`; therefore this checkout's `origin/main` tracking ref remains stale. No local rebase, reset, or checkout was performed.


## Continuous Runtime audit plan — 2026-10-24

### Reconciled baseline

- Canonical repository: `ordaxsystems/ordax-runtime`; `main` is `d4d450eb8e52391e8edaf0f769979e76353ebdce`. GitHub search reports no open Runtime pull requests. The preserved `preserve/runtime-audit-worktree-20261024` branch is a recovery snapshot and is not a merge candidate.
- The 2026-10-09 installer-composition gap in issue #67 was addressed structurally by Runtime PR #74, merged at `6a131aec1865f3bc304843dc104aa8a75325cb2e`: the official Windows entrypoint/navigation now uses the canonical Apps Studio Conversation/Electron presentation (Apps commit `556f4ef`), and its Windows build/install smoke passed on the PR candidate. The current Runtime source lock pins Apps Studio 0.15.9 at `a297b691d5a91ea0370d80f32533931596dfe5f6`. Runtime PR #231 is a separate change for Control Plane endpoint provenance; it did not integrate the Conversation UI. Issue #67 remains open for real provider-session, project-preview, explicitly granted MCP-action, signed-release, and physical-device evidence; CI smoke is runner evidence only.
- Native OS execution is composed through `ordax_dev_agent.native_main` and the existing Runtime dispatch. Native does not introduce another Control Plane, identity owner or action queue. The current offline Buildroot probe does not use a real device credential or network and explicitly leaves full dependency closure, device install and device execution unproven.
- Runtime issue #1 is closed; issue #7 remains dependent on the OS-owned Studio Runtime v3 result-binding contract. #49 awaits production signing custody; #61 awaits Platform production readiness; #127 awaits canonical grant/discovery behavior and authorized E2E; #149 awaits the Owner/Dev rollback evidence; #184 awaits the governed Platform contract and Apps host integration.

### Work sequence

1. **Audit and SSOT (in progress):** map every Runtime entrypoint, MCP adapter, action registry, authorization check, updater and packaged process to its consumers and canonical owner. Keep compatibility aliases until consumer evidence supports removal. Record only demonstrated duplication or dead code.
2. **MCP execution and recovery:** trace authenticated session → device/project scope → dispatch → typed local policy → action → terminal receipt/status → audit evidence. Add or adjust focused positive and negative tests for timeout, reconnect, duplicate submission, revocation and stale readiness. Change Runtime-owned code only; do not copy Platform authorization or audit services.
3. **Native OrdaX OS:** reconcile the Runtime lock and dependency closure against the exact OS-owned Buildroot ABI and package recipes. Require the OS owner’s verified artifact and lifecycle/identity contract before changing pins or claiming installability. Then prove packaged bootstrap, OS-supervised lifecycle, fresh presence and one explicitly authorized read-only action with result recovery on a real target.
4. **Windows recovery and release:** keep Owner/Dev apply fail-closed until issue #149 proves recovery on disposable Windows fixtures and the authorized device gate is satisfied. Keep production publication closed until issue #49 has protected signing custody and install-smoke of the final signed bytes. Keep the endpoint lock unchanged until Platform issue #61’s canonical deployment reference is production-ready.
5. **Cross-project resources and grants:** progress #127 and #184 only with Platform’s authorization contract and Apps’ consumer contract. Runtime must never infer a remote grant from local `full_access`, a project id, device presence or a successful health endpoint.
6. **Integration and repository hygiene:** for each Runtime change, branch from the latest main, preserve any pre-existing work, run the applicable local tests and required CI, reconcile main before merge, then record commit/check/evidence limits here. Remove a branch only when merged or deliberately retired, its commits are preserved, no PR depends on it, and a supported deletion path is available.

### Completion evidence

Track **source tests**, **hosted CI**, **cross-service integration**, and **physical device execution** separately. A workstream is complete only when its own acceptance evidence exists at every applicable layer, security remains fail-closed, resource behavior is bounded, and the canonical owner confirms external contracts. A CI pass alone does not close a physical or production gate.

### Immediate execution status

- Reconciled current main, open PRs, current issue owners and the previously stale #67 summary.
- Native OS install/MCP remains unverified while the authorized notebook is offline and the Buildroot dependency closure is not proven. No device action or permission change was made.
- Next Runtime-owned pass: finish the entrypoint/adapter/consumer map, then select the first reproducible code defect with an owner-local fix and regression test. External gates remain explicit handoffs, not invented Runtime work.

### Checkpoint de continuidade — 2026-10-24

- A base atual desta revisão é a `main` em `fe2c9c1acc47e21a8ef28bc372d8575673859a3f`, após a integração do plano de auditoria pela PR #238. A PR #239 está aberta, baseada nessa `main`, para SSOT do servidor MCP Product e integridade da auditoria de execução.
- A PR #239 remove o segundo `FastMCP` não utilizado de `product_mcp_handlers.py`, preserva o entrypoint histórico como delegação ao servidor canônico, registra `local_action_failed` quando a execução/sanitização lança exceção e grava a fase terminal da auditoria antes de responder. A documentação MCP e regressões de fonte foram incluídas.
- A revisão encontrou que resultados `ActionResult(ok=False)` recebiam a razão de auditoria `local_action_completed`; agora a razão acompanha o resultado. O gateway também não propaga detalhes da exceção de adaptador para a resposta remota.
- Head da PR: `04322de037e151b7cb1bdee1ac14db7e6b5b34be` no momento deste checkpoint. Workflows desse e de heads anteriores não devem ser misturados: só checks do head final podem autorizar merge. Nenhuma execução de teste local foi alegada; o checkout Runtime disponível está atrasado e contém alterações preservadas.
- A alteração não muda grants, identidade, fila, autorização remota, política local ou donos de auditoria da Platform. Ela não prova uma chamada MCP de produção.
- O Buildroot/Native continua sendo validação de composição/source; a instalação e execução sob o supervisor real do OrdaX OS não estão provadas. O notebook autorizado estava offline no último inventário, portanto não houve validação física nem ação nele.
- Após o CI final, reconciliar o head/base e integrar a PR #239 se todos os gates exigidos passarem. Em seguida, reavaliar os limites de espera/reconexão do Product MCP e manter separados CI Windows, instalação física, MCP ao vivo e OS Native.

### Checkpoint pós-merge da PR #239 — 2026-10-24

- `main` foi atualizado para `0b5e07787ca4d0d765c100c4ec1b0b67527dd1c2` pela integração squash da PR #239. Ela removeu a segunda registry FastMCP do Product, preservou os nomes de compatibilidade e passou Runtime Contracts, Windows Product Build/Install Smoke, Native Product Runtime Candidate e Native Buildroot Source Compatibility no head final. Gates de assinatura/publicação permaneceram ignorados; não fazem parte desta mudança.
- O push de `main` passou Native Buildroot Source Compatibility, Native Product Runtime Candidate, OrdaX Agent Recovery e Runtime Contracts. Windows Product Build/Install Smoke ainda estava em execução neste checkpoint. O resultado do push precisa ser conferido separadamente.
- O fluxo MCP tem fronteiras distintas: `ordax_dev_agent.mcp_server` possui a registry local compartilhada; o entrypoint Device é um shim para essa mesma registry; `ordax_studio.mcp_server` compõe ferramentas adicionais sobre ela; `product_mcp_server` possui a única registry MCP do adaptador Product remoto, e `product_mcp_handlers` mantém handlers tipados sem outra registry.
- `mcp_blender_unity` foi mantido. O pacote fornece configuração/processo importados por vários adapters Runtime, e os scripts `mcp_unity_compile.py` e `mcp_unity_capture.py` ainda invocam seu servidor para ferramentas Unity específicas. Não é uma duplicação comprovadamente sem consumidores.
- Não há PR Runtime aberta depois da integração. O branch da PR #239 permanece como histórico remoto; esta conexão GitHub não oferece operação de exclusão de branch, então nenhum ref foi movido ou apagado. Branches antigos só devem ser removidos com evidência de integração/preservação e operação de deleção suportada.
- O código Native reutiliza o dispatch Runtime, mas o Buildroot não comprova a instalação e o ciclo de vida sob o supervisor real. O notebook autorizado continua sem evidência online/execução nesta auditoria. Windows CI também não substitui teste físico ou uma sessão MCP com autorização real.
- Próximos gates: confirmar o Windows push run; continuar o mapa de erros/resultados e recuperação do MCP com regressões focadas; manter o OS responsável por ABI, dependências Buildroot e ciclo de vida, Platform por identidade/grants/fila/auditoria remota e Apps por clientes/consentimento. Não alterar os repos desses owners nem inferir autorização.

### Checkpoint pós-merge da PR #241 — 2026-10-24

- `main` avançou para `d12f28da36f6520b5118691d92729fcef801c804`. PR #241 foi integrada por squash após Runtime Contracts, Windows Product Build/Install Smoke, Native Product Runtime Candidate e Native Buildroot Source Compatibility passarem no head final.
- A alteração mantém a resposta Product remota de falha limitada a `error_code` validado e `retryable` booleano, quando presentes; elimina resumo e campos arbitrários do adaptador. O teste injeta um caminho local em `PermissionError` e verifica ausência desse valor na resposta, junto com o registro da falha terminal.
- Os workflows de push no commit `d12f28d` passaram: Runtime Contracts, Windows Product Build/Install Smoke, Native Candidate, Native Buildroot Source Compatibility e OrdaX Agent Recovery. Assinatura, publicação e smoke de bytes assinados ficaram pulados; não são evidência de release assinado.
- Não há PR Runtime aberta neste checkpoint. As issues ainda abertas são #7, #49, #61, #67, #127, #149 e #184; continuam dependendo dos owners externos/integração e das evidências físicas já descritas acima.
- O código Native continua candidato de composição/source: sem Buildroot dependency closure compatível e sem instalação/supervisão em dispositivo físico. O notebook autorizado estava desligado, portanto MCP ao vivo no OrdaX OS segue sem prova. O Windows smoke é runner descartável, não instalação no notebook do usuário.
- Próximo passe Runtime: verificar classificação de timeout/reconexão e semântica de request id do Product MCP somente onde o contrato e um teste reproduzível indicarem defeito. Não transformar erro transitório em nova submissão; não confundir presença/heartbeat com dispatch readiness.



### Checkpoint pós-PR #242 e revisão MCP — 2026-10-24

- A PR #242 integrou este checkpoint à `main` em `9f358afbaef8051863939bd1ab7d10028d70b729`. Os gates de Runtime Contracts, packaging, Product MCP minimum SDK, Native composition, Windows installer build e install-smoke passaram no head da PR. Release governance foi skipped; isso não é evidência de release assinado.
- A revisão de `ProductRemoteClient` não encontrou defeito reproduzível de retry/timeout: submissão ambígua preserva o `request_id` e proíbe replay; consultas de status mantêm o mesmo id e o prazo monotônico. Nenhuma mudança de código foi justificada nesta passagem.
- A descoberta de alvos foi somente leitura: o notebook OrdaX autorizado continua offline; o desktop de desenvolvimento reportado online tem apenas descoberta `product.target.read`, sem grant de computador para uma ação de teste. Nenhuma ação foi enviada. Presença reportada não confirma dispatch readiness nem execução MCP.
- A afirmação anterior deste relatório sobre a PR #231 foi corrigida: a integração visual pertence à PR #74; #231 fixou proveniência do Control Plane. As provas atuais continuam separando CI Windows, MCP ao vivo e Native OS físico.


### Inventário de branches e PRs — 2026-10-24

Este inventário foi feito sobre o repositório canônico `ordaxsystems/ordax-runtime`, com `main` em `3d98db0ce6fcdb1c465f7a89e3f862566df09801`. A API confirmou 46 refs de branch e nenhuma PR aberta antes desta passagem. A única PR aberta agora é #244, criada para corrigir a recuperação da identidade do dispositivo.

- **20 refs são exatamente os heads de PRs já mescladas:** `audit/studio-runtime-platform-owner-terms-20261019` (#136); `docs/preserve-runtime-worktree-20261024` (#237); `docs/runtime-audit-correct-studio-lineage-20261024` (#243); `docs/runtime-audit-followup-20261023` (#235); `docs/runtime-audit-integrated-pr-status-20261023` (#226); `docs/runtime-audit-plan-20261024` (#238); `docs/runtime-audit-post-226-review-20261023` (#227); `docs/runtime-bootstrap-closure-audit-20261024` (#228); `docs/runtime-checkout-provenance-20261023` (#236); `docs/runtime-post-239-audit-20261024` (#240); `docs/runtime-post-241-audit-20261024` (#242); `feat/runtime-two-phase-issuance-client-20261024` (#229); `fix/electron-product-authority-origin` (#114); `fix/product-action-result-success-boundary-20261024` (#230); `fix/product-mcp-canonical-ingress-default-20261024` (#232); `fix/product-mcp-error-redaction-20261024` (#241); `fix/windows-control-plane-endpoint-provenance-20261024` (#231); `perf/product-action-poll-backoff-20261023` (#234); `perf/product-mcp-connection-pool-20261023` (#233); `refactor/product-mcp-single-server-20261024` (#239).
- **14 refs pertencem a PRs fechadas sem merge:** #82 `chore/studio-0.14.2-mcp-first`; #63 `experimental/studio-native-chatgpt-web-bridge-20261008`; #57 `feat/codex-app-server-transport-20261008`; #18 `feat/owner-project-browser-grants`; #205 `feat/runtime-workspace-resource-authority-20261022`; #208 `feat/workspace-two-phase-issuer-client-20261022`; #139 `fix/mcp-text-patch-newline-20261019`; #166 `fix/report-jsonb-nul-before-first-delivery-20261020`; #28 `fix/upgrade-quiesce-packaged-processes`; #98 `fix/windows-provenance-package-gate-v1`; #26 `fix/windows-upgrade-orphan-runtime-child`; #90 `release/studio-0.15.4-source-lock`; #12 `test/cleanup-post-cutover-control-plane-ownership`; #32 `update/studio-0.5.5-computer-access`. As razões de fechamento indicam incompatibilidade, sobreposição, experimento não habilitado ou conteúdo supersedido. Os diffs e discussões continuam nas PRs fechadas.
- **Sete refs sem PR estão integralmente atrás de `main` (zero commits exclusivos):** `feat/canonical-project-browser-scope`, `fix/canonical-owner-mcp-grants`, `fix/studio-loopback-oauth-return-20261015`, `fix/trusted-product-client-ingress`, `fix/windows-bundle-version-independent-studio-20261020`, `fix/windows-clipboard-pointer-signatures-20261020` e `fix/windows-distribution-independent-version-20261020-b`. As duas branches de versão apontam para o mesmo SHA.
- **A branch `fix/silent-upgrade-runtime-restart-20261020` aponta além do head mesclado em #164.** O diff atual contra `main` contém apenas ajustes de CI/smoke de instalação; o smoke atual em `main` tem verificações de timeout mais completas e os checks de instalação passaram no último head. Nenhuma funcionalidade exclusiva dessa ponta foi identificada como necessária.
- **A branch `fix/canonical-device-bootstrap-ingress` tem um commit exclusivo sem PR.** `main` já usa o endpoint Product, mas ainda duplicava a resolução/validação em recuperação de identidade. A PR #244 centraliza essa regra com o enrollment, preserva HTTPS obrigatório na recuperação e adiciona regressão. Só considerar a branch antiga aposentada após integrar e validar #244.
- **A branch `preserve/runtime-audit-worktree-20261024` é um snapshot explícito de um commit de trabalho local.** A implementação de prontidão foi substituída por uma versão posterior em `main`; o relatório antigo é histórico e difere do relatório atual. O checkout local de recuperação continua sujo e não foi removido. Não apagar esse último ref de recuperação antes de validar a preservação final.
- **Estado da limpeza:** nenhuma branch foi apagada. O conector GitHub desta sessão não disponibiliza exclusão de refs; não simular exclusão movendo branches para `main`. Primeiro terminar #244 e preservar o snapshot; depois remover somente refs aposentadas com confirmação do estado final e operação suportada.

Os grupos somam 45 branches não-main, além de `main`, após a abertura da PR #244. Esta é uma fotografia de auditoria, não uma declaração de que o MCP ou os dispositivos físicos foram validados.

### Checkpoint pós-PR #244 e limpeza remota — 2026-10-24

- O repositório canônico `ordaxsystems/ordax-runtime` foi reconciliado em `a9e15e8d7d199e92f981cfbf475cceef10c70f72`. A PR #244 integrou a recuperação de identidade do dispositivo usando o resolvedor canônico de endpoint Product e mantendo HTTPS obrigatório. Os testes de regressão cobrem o caminho fechado para falhas de origem.
- Os gates do head final da PR passaram: Runtime Contracts, Windows Product Build/install-smoke/Owner-Dev candidate, Native Buildroot Source Compatibility, Native Product Runtime Candidate e OrdaX Agent Recovery. Governança de release, assinatura Authenticode, smoke de bytes assinados e publicação foram pulados; não há prova de release assinado ou cutover.
- A API do GitHub confirmou somente a branch `main` e zero PRs abertas após o merge. As 45 branches não-main inventariadas foram removidas depois da confirmação explícita do proprietário. As PRs fechadas preservam seus diffs e discussões. O checkout local antigo continua separado, atrasado e sujo; não foi atualizado nem alterado.
- O rascunho de prontidão encontrado nesse checkout já está supersedido. A `main` mantém um único `product_dispatch_readiness`, exige ação registrada, pareamento, transporte conectado e heartbeat Product finito com até 60 segundos, e a camada Studio separa readiness do dispositivo de presença HTTP do MCP. `tests.test_runtime_operational_status` passou com o Python empacotado desta sessão. `tests.test_ordax_studio_web` não importou porque o ambiente de teste não contém `httpx`; essa tentativa não é resultado negativo do código. Não foi aberta PR duplicada.
- A confirmação de readiness é apenas do lado do dispositivo. Ela não comprova OAuth/grant de cliente, ação remota, resultado/auditoria da Platform ou execução física. No último inventário, `DESKTOP-COHT67R` permitia somente leitura de alvos e o notebook `ordax` estava offline; nenhuma ação MCP foi enviada.
- O Native continua sem prova de runtime operacional no OrdaX OS: a verificação CI do candidato ainda relata ABI independente indisponível e dependências ELF externas, e não prova closure completa, pacote instalado, autorização de instalação ou execução no dispositivo.
- Issues abertas: #7 depende do contrato de resultados OS/Runtime; #49 de custódia e assinatura de produção; #61 da readiness do Control Plane pela Platform; #67 de E2E real de Apps/sessão/MCP e dispositivo; #127 dos grants/catálogo da Platform e UX de consentimento Apps; #149 da recuperação Owner/Dev física; #184 da integração governada Platform/Apps. O handoff de #127 identifica Platform #211/#238/#220; Runtime mantém o diagnóstico read-only e não cria grants.

Este checkpoint substitui as linhas de inventário de branches anteriores que diziam que a limpeza ainda estava pendente. Ele não altera os gates de integração, assinatura ou dispositivo físico acima.


### Checkpoint PR #246 — prazo de espera do Product MCP — 2026-10-24

- A revisão foi iniciada após reconciliar `main` em `c8dcceef48a088128d7c3e0bdbe1b7e043c190f2`, sem PRs abertas e com somente a branch `main`. O notebook `notebook ordax` permanece offline; ele tem um grupo `full-computer-control`, mas não pode receber teste físico enquanto desconectado. `DESKTOP-COHT67R` estava online, mas só possui `product.target.read`. Nenhum cliente MCP foi invocado contra esses dispositivos.
- PR #246 (`fix/product-action-deadline-after-parse-20261024`) corrige um limite de prazo no cliente Product MCP. A leitura HTTP já verificava o deadline monotônico durante o consumo do corpo, mas `action()` decodificava e validava o receipt depois dessa verificação. Um receipt terminal validado após o prazo podia retornar sucesso atrasado. Agora a validação final verifica o mesmo deadline; o timeout conserva o `request_id` e `wait_action()` o comunica como conclusão desconhecida. Nenhum POST é repetido e nenhum novo request é criado.
- `tests/test_product_action_recovery.py` contém regressão determinística que avança o relógio durante a decodificação e espera `ProductActionWaitTimeout` com o ID original. A reprodução local isolada passou; `py_compile` e `git diff --check` passaram. A suíte Python focada não foi importável no ambiente local, que não tinha o MCP SDK. A suíte Runtime Contracts executada no CI do head `d39029b9f2db5773845c24e21e97d5cd42176f09` passou, incluindo os contratos de recuperação Product MCP.
- No mesmo head passaram Runtime Contracts (run 816), Product MCP Minimum SDK, Native Runtime Composition, Native Product Runtime Candidate (run 196), Native Buildroot Source Compatibility (run 50), Windows Product Build (run 680), Packaging Contracts e Install Smoke. Release governance, attestation de candidato, assinatura Authenticode, smoke de bytes assinados e publicação foram skipped; CI não representa uma release assinada.
- Esses resultados verificam código, composição e instalador descartável em CI. Não houve sessão MCP autenticada contra o serviço, ação de computador, instalação física no notebook, nem instalação/execução sob o supervisor real do OrdaX OS. Issues #7, #49, #61, #67, #127, #149 e #184 continuam abertas nos limites e owners documentados acima.
- Nenhum arquivo de `ordax-os`, `ordax-apps` ou `ordax-platform` foi alterado. PR #246 foi integrada por squash em 2026-10-24 no commit `931fc988ae12aeffe090cf50a3784813733b10a5`. Os checks da PR passaram no head `8aaa878a78304cd85c0458934a9a42ee6046fc84`; os workflows de push pós-merge estavam em execução neste checkpoint. Depois do merge, a API do GitHub confirmou zero PRs abertas e somente a branch `main`; a branch de trabalho mesclada foi removida conforme a autorização do proprietário.

### Continuidade após PR #247 — contrato de resultado v3 — 2026-10-24

- main foi reconciliada no commit 7f7963f11e1892b692c6f00662a559717a6d7dfd. PR #247 atualizou o checkpoint pós-#246 e foi integrada por squash. A API GitHub confirmou zero PRs abertas e somente main; as duas branches de trabalho mescladas foram removidas. Os checks da PR #247 passaram em Runtime Contracts, Native Runtime Composition, Product MCP Minimum SDK e Windows Packaging/Build/Install Smoke. Os workflows de push pós-merge também passaram: Runtime Contracts (run 40093348957) e Windows Product Build (run 40093348988, incluindo build-installer, install-smoke e attest-owner-dev-candidate); release-governance, sign-release, signed-release-smoke e publish-release ficaram skipped. No head da PR #247, a atestação também havia sido skipped.
- Leitura somente de ordaxsystems/ordax-os/main, commit cc2928dcc98274821c13c38b40e2ae87af8ac301: system/contracts/device-action-result.mjs valida ordax.device-action-result/1 com envelope JSON normalizado de até 1 MiB, profundidade 6, 1024 itens por array, 256 campos por objeto, chaves até 128 caracteres e strings até 512 KiB; rejeita credenciais e saída não nula quando o receipt não terminou em succeeded. O port ordax.studio-runtime/3 valida a resposta contra o request original e o contexto autenticado de actor, Space, projeto, device e client.
- No Runtime em 7f7963f, ProductRemoteClient limita o corpo HTTP completo a 4 MiB, enquanto product_mcp_handlers._action_receipt devolve result sem normalização adicional quando o status é succeeded. Isto identifica uma diferença de fronteira de resultado; não demonstra exploração nem substitui prova de resposta real da Platform.
- A issue Runtime #7 confirma que a resposta Product MCP atual não fornece o binding v3 completo. O Runtime não deve fabricar esse binding a partir de request_id ou de dados do caller. Handoff: OS deve definir como o request v2 original e seu contexto validado chegam ao consumidor; Platform deve expor/correlacionar o resultado persistido com essa binding autenticada; então Runtime pode implementar a adaptação e validação acordadas. Nenhum arquivo de ordax-os, ordax-apps ou ordax-platform foi alterado.
- Uma duplicação Runtime-owned foi removida no cliente Product MCP: _UUID e _CLIENT_ID compilavam a mesma expressão UUID, e _client_id repetia a normalização de _uuid. Ambos agora compartilham o único validador sem mudar a mensagem de erro nem o formato aceito. Os testes existentes de cliente e emissão em duas fases cobrem os caminhos consumidores. A tentativa local de executar esses módulos não importou porque o Python empacotado não contém httpx; a compilação sintática passou. Na CI do head a2fd755, test_product_remote_client passou no job Runtime Contracts e test_workspace_two_phase_issuance_client passou no job Native Runtime Composition.

Os resultados de CI validam contratos e instalador descartável; não comprovam sessão MCP autenticada, ação em dispositivo físico nem execução sob o supervisor real do OrdaX OS.

### Resultado pós-PR #248 — 2026-10-24

- PR #248 foi integrada por squash em main no commit 58faf7497bd580edf91d7bda6587516296bb34fd. Os checks do head a2fd7552c4837864aead4468a1575c2c31497354 passaram: Runtime Contracts, Product MCP Minimum SDK, Native Runtime Composition, Native Product Runtime Candidate, Native Buildroot Source Compatibility, Windows build/packaging/install-smoke e OrdaX Agent Recovery.
- No push pós-merge, Runtime Contracts (run 40095528984), Native Product Runtime Candidate (40095529042), Native Buildroot Source Compatibility (40095528978), OrdaX Agent Recovery (40095528997) e Windows Product Build (40095528992) passaram. O smoke de instalação e attest-owner-dev-candidate passaram; release-governance, sign-release, signed-release-smoke e publish-release ficaram skipped. A atestação do candidato Owner/Dev não equivale a assinatura de release.
- Após a integração, a API do GitHub confirmou zero PRs abertas e somente main; a branch da PR #248 foi removida. O commit remoto atual é 58faf7497bd580edf91d7bda6587516296bb34fd.

### Continuidade após PR #249 — respostas Product JSON sem chaves duplicadas — 2026-10-24

- A revisão foi reconciliada com `main` em `c436ca9b6b1a3a05f13cbaa33cecac2a4d81b47d`; não havia PR aberta e o remoto continha somente `main`. Os workflows pós-merge Runtime Contracts (run 40096713275) e Windows Product Build (run 40096713329) terminaram com sucesso.
- Auditoria de `ProductRemoteClient._body` identificou que `json.loads` usava o comportamento padrão last-key-wins. Em uma resposta duplicada como `{"ok":false,"ok":true}`, o parser podia converter um envelope contraditório em resposta aceita; duplicatas aninhadas também eram silenciosamente sobrescritas. Isso é uma falha de interpretação demonstrável no código, não evidência de incidente no serviço.
- A correção Runtime adiciona um único `object_pairs_hook` para rejeitar chaves repetidas em qualquer objeto JSON, sem alterar grants, identidade, retry, request IDs ou formato das respostas válidas. A regressão cobre a contradição no envelope e uma duplicata em objeto aninhado.
- `py_compile`, `git diff --check` e uma reprodução isolada que executa diretamente o parser alterado passaram. A suíte `tests.test_product_action_recovery` não pôde ser importada localmente porque o Python empacotado desta sessão não contém `httpx`; a validação hospedada da PR ainda será necessária. Nenhum resultado de CI desta mudança, integração de serviço ou execução física é alegado neste checkpoint.
- A rejeição de chaves duplicadas é compatibilidade de parsing HTTP do Runtime; não resolve o binding ausente do contrato Studio Runtime v3 em #7. OS/Platform seguem responsáveis pelo handoff já registrado, e nenhuma alteração foi feita nesses repositórios.

### Resultado pós-PR #250 — CI e disponibilidade dos dispositivos — 2026-10-24

- PR #250 foi integrada por squash às 13:48:59Z no commit `3202443cb139cdd5542c76e1aaeffdd35d43885a`. No head `854f2bde44ded5107a26fdcf514fa7ad91cfea54`, passaram Runtime Contracts (run 40099229355, incluindo a suíte Product MCP), Native Product Runtime Candidate (40099229359), Native Buildroot Source Compatibility (40099229379) e Windows Product Build (40099229367), incluindo `build-installer` e `install-smoke`. Governança de release e assinatura/publicação ficaram skipped.
- No push pós-merge, passaram Runtime Contracts (40099764685), Native Product Runtime Candidate (40099764664), Native Buildroot Source Compatibility (40099764653), OrdaX Agent Recovery (40099764630) e Windows Product Build (40099764679). O build e install-smoke do Windows passaram e `attest-owner-dev-candidate` passou; `release-governance`, `sign-release`, `signed-release-smoke` e `publish-release` ficaram skipped. A atestação Owner/Dev não é assinatura Authenticode de produção.
- A auditoria local não executou a suíte focal porque o Python empacotado não contém `httpx`; `py_compile`, `git diff --check` e a reprodução isolada do parser passaram. A validação da suíte vem dos jobs hospedados acima. Nenhuma sessão autenticada contra o Product MCP, ação executada em dispositivo físico, instalação física Windows ou execução sob o supervisor real do OrdaX OS foi demonstrada.
- No inventário read-only mais recente via `ordax_targets`, `DESKTOP-COHT67R` estava online (`last_seen_at` `2026-10-24T13:38:58.960Z`), mas com somente `mcp-discovery/product.target.read`. `notebook ordax` estava offline (último `last_seen_at`: `2026-10-23T04:43:51.376Z`), apesar do grupo registrado `full-computer-control`. Nenhuma ferramenta de ação foi invocada. Portanto MCP/execução Native no OrdaX OS continua sem prova física; o estado online do desktop tampouco habilita uma ação de computador.
- Issues Runtime #7, #49, #61, #67, #127, #149 e #184 continuam abertas na última leitura: respectivamente contrato de resultado OS/Platform, custódia de assinatura, readiness de produção Platform, E2E real Apps/MCP, grants canônicos, rollback físico Owner/Dev e integração governada de workspace. Mantêm-se os handoffs já documentados; nenhuma autorização foi inferida e nenhum arquivo de `ordax-os`, `ordax-apps` ou `ordax-platform` foi alterado.
- Após #250, antes desta atualização documental, o remoto e o clone isolado foram verificados com apenas `main` e zero PRs abertas; a branch mesclada foi removida. A PR atual de auditoria tem branch temporária, que deve ser removida após merge. Os diffs e discussões das PRs mescladas continuam preservados no GitHub.

### Auditoria do limite de varredura de workspace — 2026-10-24

- A issue #184 segue aberta e os comentários mais recentes confirmam que o port read-only está na `main`, mas não há picker/consentimento composto no host, emissão de grants habilitada nem integração real com Apps. A capability continua indisponível até esses gates externos e o E2E autorizado.
- Um novo pente-fino no port encontrou que `_snapshot` limitava arquivos regulares e profundidade, mas convertia cada diretório inteiro em uma lista ordenada antes de contabilizar arquivos. Uma pasta com milhares de diretórios vazios podia exceder o limite de memória/CPU sem aumentar `fileCount`; a descrição “bounded” não cobria esse caso.
- A correção introduz um teto global de 40.000 entradas enumeradas, contado antes de retê-las ou ordená-las. O limite inclui diretórios, arquivos e entradas ignoradas como links/reparse points; excedê-lo falha com `workspace_scan_limit`. O teto de 20.000 arquivos e o comportamento de revisão determinística permanecem.
- A regressão usa quatro diretórios vazios e um teto injetado de três, confirmando que o limite não depende da contagem de arquivos. Os testes focados `tests.test_project_workspace_port` e `tests.test_workspace_resource_selection` passaram localmente: 25 testes, 3 skips por limitações de symlink/junction do ambiente. Uma tentativa local mais ampla executou 44 testes, mas não pôde importar `tests.test_owner_project_scope_resolver` nem `tests.test_workspace_device_live_authority` porque o Python empacotado não tem `httpx`; não é falha de asserção do Runtime.
- PR #252 (`fix(workspace): bound snapshot directory entries`) foi squash-merged em `main` no commit `5147247f3fca189fdec032fdbb45a80a402dfbf4`; seu head foi `f02471ccf076f73fd1a5b96ae42c0bb28873475b`. Na PR passaram Runtime Contracts (run 40103906810), Native Product Runtime Candidate (40103906806), Native Buildroot Source Compatibility (40103906813) e Windows Product Build (40103906816), incluindo build-installer e install-smoke. Release governance, assinatura, atestação Owner/Dev, signed-release-smoke e publicação ficaram skipped na execução da PR.
- No push pós-merge passaram Runtime Contracts (40104350999), Native Buildroot Source Compatibility (40104350998), OrdaX Agent Recovery (40104351032), Native Product Runtime Candidate (40104351095) e Windows Product Build (40104351021), incluindo build-installer, install-smoke e `attest-owner-dev-candidate`. Release governance, assinatura Authenticode, smoke de bytes assinados e publicação ficaram skipped. Esses resultados validam código/CI e instalador descartável; não são integração real Host/Platform/Apps, instalação física Windows, sessão MCP autorizada ou execução no supervisor do OrdaX OS.
- Após a integração e remoção da branch da PR #252, a API confirmou `main` em `5147247f3fca189fdec032fdbb45a80a402dfbf4`, zero PRs abertas e somente a branch `main`. Nenhum arquivo de `ordax-os`, `ordax-apps` ou `ordax-platform` foi alterado.

### Correção de corrida de reparse point no snapshot — 2026-10-24

- A revisão adversarial do workspace revelou uma fuga TOCTOU que os testes estáticos de symlink não cobriam: no Windows, substituí uma pasta normal por uma junction exatamente entre o `lstat` da entrada e o `scandir` do diretório. A implementação anterior seguiu a junction e contou um arquivo fora da raiz concedida (`fileCount=1`). O snapshot não retorna nomes nem conteúdo, mas a leitura de metadados já violava o limite de recurso.
- O port agora enumera em POSIX por descritores de diretório abertos com `O_DIRECTORY | O_NOFOLLOW`, usando `scandir(fd)`, `stat(..., dir_fd=...)` e abertura relativa sem seguir links. No Windows, abre a raiz e cada diretório com `FILE_FLAG_OPEN_REPARSE_POINT`, valida os atributos/tag do handle e mantém o handle de leitura aberto sem compartilhamento de DELETE durante a enumeração; troca/rename concorrente falha fechado. O Git também usa a raiz e o diretório `.git` atestados por handles/FDs.
- Regressões novas simulam a troca de symlink durante a enumeração em POSIX, verificam o caminho `cwd` com descritor herdado para Git e tentam a troca para junction no Windows. A validação local Windows passou em `py_compile`, `git diff --check` e 28 testes focados (`tests.test_project_workspace_port` e `tests.test_workspace_resource_selection`), com cinco skips: três testes estáticos de symlink que exigem Developer Mode e dois testes específicos de POSIX. O teste Windows de concorrência executou localmente.
- A primeira execução hospedada da PR #254 encontrou que `subprocess.Popen` rejeita um descritor inteiro como `cwd`. A correção passou a usar `/proc/self/fd` ou `/dev/fd` enquanto preserva o descritor com `pass_fds`; a regressão cobre a configuração e o fluxo real de Git é exercitado pelo job Linux.
- No head final `e839498500bc640b439f1c87888ca449872eace6`, todos os checks obrigatórios da PR passaram: Runtime Contracts e Native Runtime Composition (run 40106698726), Native Product Runtime Candidate (40106698638), Native Buildroot Source Compatibility com auditorias/probe (40106698713), Windows Product Build com `build-installer` e `install-smoke` (40106698975), além dos contratos de empacotamento e SDK mínimo. Governança de release, Authenticode, atestação Owner/Dev, smoke de artefato assinado e publicação ficaram skipped conforme esperado para uma PR sem release. O primeiro resultado Linux falho é supersedido pela execução verde do head corrigido.
- A branch partiu da `main` `66f348895e764ae9dfcf86368d6241d6ee3a40dc`, reconciliada após #253; naquele início havia zero PRs abertas e apenas `main` no remoto. Os resultados verdes são validação local, CI hospedado e install-smoke em runner Windows descartável. Eles não provam integração real Host/Platform/Apps, sessão MCP autorizada, execução no notebook, instalação física Windows nem execução sob o supervisor real do OrdaX OS; a issue #184 continua aberta nos gates de picker/consentimento, grant e composição autenticada.

### Continuidade: wake reconnect e disponibilidade MCP — 2026-10-24

- Após o merge da PR #254, `main` avançou para `476dbab17232d8e0a5cb840ca5e05d633e226e89`. A CI pós-merge passou em Runtime Contracts, Runtime Composition, Native Candidate, Buildroot Source/Probe, Windows Product Build, build-installer, install-smoke e atestação de candidato Owner/Dev. Governança de release, assinatura Authenticode, signed-release-smoke e publicação ficaram skipped; CI ainda não é instalação física nem E2E contra um agente sob o supervisor do OS.
- Nova revisão do wake listener reproduziu que dois handshakes aceitos seguidos de desconexão imediata esperavam `[30, 30]` segundos: o backoff exponencial existente era resetado no momento do handshake, antes de saber se a sessão permaneceu estável. Em reconexões curtas repetidas, isso mantinha tentativas a cada 30 segundos em vez de limitar a carga do control plane ao teto de cinco minutos. Esta branch só altera o transporte advisory; SQL claim continua sendo a autoridade de ações.
- A correção mantém o backoff após conexões de curta duração e só o reinicia depois de 60 segundos de conexão estável; regressões determinísticas cobrem `[30, 60]` e o reset após uma sessão estável. `py_compile`, `git diff --check` e um harness offline das duas sequências passaram. A suíte formal local não pôde importar `tests.test_product_wake_signal` porque o Python desta sessão não tem `httpx`.
- No head de código `3c9ee556725e7f882799c38718c34b83e9edbf45`, passaram Runtime Contracts e Native Runtime Composition (run 40108644806), Native Product Runtime Candidate (40108644850), Native Buildroot Source Compatibility com auditorias/probe (40108644863), Windows Product Build com `build-installer` e `install-smoke` (40108644836), além de packaging contracts e SDK mínimo. Governança de release, assinatura Authenticode, atestação Owner/Dev, signed-release-smoke e publicação ficaram skipped por não ser uma PR de release.
- A validação final pré-merge no head documental `f5c3ff7e7411a23f7b25d209dce513cccdcd0b62` também passou: Runtime Contracts/Composition (run 40109250847), Native Product Runtime Candidate (40109250849), Native Buildroot Source Compatibility e probe (40109250864), Windows Product Build com `build-installer` e `install-smoke` (40109250886), além de packaging contracts e SDK mínimo. Jobs de governança, assinatura, atestação Owner/Dev, signed-release-smoke e publicação ficaram skipped por não ser uma PR de release.
- Consulta somente de leitura a `ordax_targets` às 15:37:58Z: `DESKTOP-COHT67R` aparecia online, mas `grants` estava vazio e o único grant group era `mcp-discovery` com `product.target.read`; portanto, não havia capacidade concedida para provar ação MCP. `notebook ordax` aparecia offline (último contato 2026-10-23T04:43:51.376Z), e `Meu OrdaX` também offline. Nenhuma ação, terminal, leitura de arquivos ou tentativa de elevar grants foi feita. A presença online do Desktop não significa execução de computador autorizada; a disponibilidade do notebook precisa ser reavaliada quando voltar à rede.
- As issues #127 e #184 continuam abertas por gates de Platform (grants/catálogo/dispatch) e Apps (consentimento/composição). A Runtime não altera a emissão desses grants nem tenta executar contra dispositivos sem a autorização e conectividade necessárias.

### Redução de I/O ocioso no supervisor de processos — 2026-10-24

- Reconciliei novamente o checkout canônico `ordaxsystems/ordax-runtime`: `main` em `fc5c17fb3f208d92a919d9dd8aedfce343692e3e`, árvore limpa antes da branch, apenas `origin/main` e nenhuma PR aberta. Permanecem sete issues abertas (#7, #49, #61, #67, #127, #149 e #184), sem evidência nova que permita ao Runtime resolver os gates externos de Platform, Apps, assinatura ou dispositivo físico.
- A leitura de `ProcessRuntime._pump_stdin` encontrou um loop fixo de 50 ms que reabria e lia o journal de cada processo persistente mesmo quando vazio: 20 aberturas por segundo por processo ocioso.
- Benchmark local controlado no Windows deste host, com processo filho simulado ocioso por ~2,2 s: polling fixo fez 43 aberturas (19,5/s); a implementação adaptativa fez 12 (5,6/s), redução observada de 71% nas aberturas do journal. Isso mede operações de abertura, não CPU/RAM total nem uso de produção.
- O poller agora começa em 50 ms, dobra somente em ciclos sem entradas e limita a espera ociosa a 200 ms. Ao encontrar entradas, volta a 50 ms antes da próxima espera. A fila continua append-only, preserva sua posição de leitura e a semântica existente de entrega/flush; não foi introduzido serviço, IPC ou autoridade nova.
- Novos testes determinísticos isolados em `tests.test_persistent_process_runtime` verificam a sequência ociosa `[50, 100, 200, 200, 200]` ms e o retorno a 50 ms depois de nova entrada. Os dois testes passaram localmente. `py_compile`, `git diff --check` e um harness com filho Windows real iniciado por `ProcessRuntime.run()` também passaram: o filho recebeu `wake-check` pelo journal e terminou com estado `exited`.
- A suíte maior `tests.test_persistent_process_actions` não importou neste ambiente porque o Python empacotado da sessão não contém `httpx`; não é falha de asserção. No head de código da PR #256 (`244d5bcbac2a2955d6a1e81c05d0f1376bda1f4d`), passaram Runtime Contracts (run 40111684581), Native Runtime Composition (40111684581), Product MCP Minimum SDK (40111684581), Native Product Candidate (40111684545), Native Buildroot Source Audit e Offline Agent Probe (40111684536), Packaging Contracts, Windows `build-installer` e `install-smoke` (40111684542). Governança, assinatura, atestação Owner/Dev, smoke de bytes assinados e publicação ficaram skipped; nenhum release assinado foi produzido. O install-smoke foi executado em runner Windows descartável: não comprova instalação física, sessão MCP autorizada, atualização do notebook ou execução sob o supervisor real do OrdaX OS.
- Nenhuma ferramenta de ação OrdaX foi invocada neste passe; nenhuma concessão, configuração ou outro repositório foi alterado. O snapshot read-only anterior continua mostrando que presença online/discovery não equivale a grant de execução.

### Continuidade: cursor do journal e pós-merge #256 — 2026-10-24

- A PR #256 entrou em `main` no commit `1ac6b88eae98d696fa9ce263022683d0136f755e`. No push pós-merge passaram ORDAX Runtime Contracts (run 40112923157), Native Product Runtime Candidate (40112923161), Native Buildroot Source Compatibility (40112923175), OrdaX Agent Recovery (40112923199) e Windows Product Build (40112923190), incluindo build-installer, install-smoke e atestação do candidato Owner/Dev. Assinatura de produção, smoke de bytes assinados e publicação ficaram skipped.
- Depois da integração, o ref da PR foi removido; a API confirmou zero PRs abertas e somente `main` no remoto. Antes de abrir a branch desta correção, o checkout local estava limpo e sincronizado com `main`.
- Nova leitura somente de `ordax_targets` às 16:40:17.752Z: `DESKTOP-COHT67R` online, mas somente com `mcp-discovery/product.target.read` e sem grants de ação; `notebook ordax` e `Meu OrdaX` offline, também sem grants emitidos. Nenhuma ação MCP foi invocada.
- Reproduzi uma corrida no journal JSONL dividindo uma única entrada em duas escritas: quando o supervisor lia o primeiro fragmento sem `LF`, avançava o cursor e descartava a entrada completa anexada depois. O processo filho não recebeu `wake`, embora o registro final estivesse válido. Isso quebrava a entrega local de stdin antes mesmo do despacho/recibo remoto.
- A correção mantém o cursor no início de qualquer linha sem terminador e só consome entradas JSONL completas. Regressão determinística divide a escrita em duas partes; um harness local também iniciou um filho descartável por `ProcessRuntime.run()`, entregou `wake-check` após o prefixo parcial e confirmou saída `exited`.
- No head `243565e6247e1250cac86a4d6722a016bc19387e` da PR #257, passaram Runtime Contracts, Product MCP Minimum SDK, Native Runtime Composition, Native Product Candidate, Native Buildroot Source Audit/Offline Agent Probe, Packaging Contracts, `build-installer` e `install-smoke`. Governança de release, atestação Owner/Dev, assinatura, smoke de bytes assinados e publicação ficaram skipped; nenhum release assinado foi produzido. O child harness local não é sessão MCP autorizada, instalação física Windows, execução no notebook nem prova do supervisor real do OrdaX OS.
- Issues #7, #49, #61, #67, #127, #149 e #184 permanecem abertas com seus gates anteriores; nenhuma mudança foi feita em ordax-os, ordax-apps ou ordax-platform.

### Resultado pós-PR #257 — cursor do journal — 2026-10-24

- PR #257 foi integrada por squash em `main` no commit `5e85548e368f8e4c64029b53dcdccad014cd75c7`. No push pós-merge passaram Runtime Contracts (run `40114612872`), Native Product Runtime Candidate (`40114612774`), Native Buildroot Source Compatibility (`40114612915`), OrdaX Agent Recovery (`40114612753`) e Windows Product Build (`40114612771`), incluindo `build-installer`, `install-smoke` e `attest-owner-dev-candidate`.
- No workflow Windows pós-merge, governança de release, assinatura Authenticode, smoke de bytes assinados e publicação ficaram skipped. A atestação Owner/Dev de candidato e o install-smoke em runner descartável não equivalem a release assinado nem a instalação física.
- Depois do merge, o head da branch foi confirmado como `243565e6247e1250cac86a4d6722a016bc19387e`; sua árvore Git era idêntica à de `main`. A branch integrada foi removida. A API confirmou zero PRs abertas e somente a branch `main`; o clone isolado ficou sincronizado e limpo no commit `5e85548e368f8e4c64029b53dcdccad014cd75c7`.
- `tests.test_persistent_process_runtime` passou localmente com três testes, `py_compile` e `git diff --check` passaram e o filho descartável Windows recebeu o registro dividido em duas escritas. A suíte maior não importou localmente por falta de `httpx`; a CI hospedada fornece os contratos do Runtime. Não houve sessão MCP de serviço, ação autorizada, instalação física Windows, teste no notebook nem execução sob o supervisor real do OrdaX OS.
- Issues #7, #49, #61, #67, #127, #149 e #184 continuam abertas nos limites e owners descritos acima. O inventário anterior ainda mostrava apenas discovery autorizado no Desktop e notebook offline; não foi tentada nenhuma ação MCP.


### Limite do job de build Windows — 2026-10-24

- A execução Windows da PR #258 (run 40115354135, head a56895250cbedfab192586242da555ff4156a503) deixou build-installer ativo por mais de 20 minutos; a definição do job não tinha timeout-minutes. Os endpoints de jobs/logs do GitHub retornaram HTTP 502/read timeout, então não há evidência suficiente para atribuir uma causa ao runner, checkout, build ou serviço do GitHub.
- Dois builds concluídos do mesmo workflow no mesmo dia duraram 3m33 (build-installer job 120931686118) e 3m11 (job 120933406075). Esta PR adiciona um limite de 20 minutos ao job — mais de cinco vezes essas durações observadas — para encerrar uma execução sem progresso indefinido e liberar o runner; o timeout não diagnostica nem corrige a causa subjacente.
- Na validação hospedada do head `2ade176ec18bdb75dfd4348d736e28745fa6b15b`, `Runtime Contracts` (run `40117311754`), `Native Runtime Composition` e `Product MCP Minimum SDK` passaram. No `Windows Product Build` (run `40117311765`), `packaging-contracts` (job `120941146101`), `build-installer` (job `120941442270`) e `install-smoke` (job `120942342286`) passaram; o limite de 20 minutos não foi acionado. Governança de release, atestação Owner/Dev, assinatura Authenticode, smoke assinado e publicação ficaram skipped. Esses resultados são CI de candidato em runners hospedados; não provam serviço MCP autenticado, instalação física Windows ou execução pelo supervisor real do OrdaX OS. Nenhuma alteração foi feita em ordax-os, ordax-apps ou ordax-platform.

### Snapshot de grants e conectividade MCP — 2026-10-24 18:34Z

- Nova consulta somente de leitura a `ordax_targets` às 18:34:24.200Z: `DESKTOP-COHT67R` estava online com apenas `mcp-discovery/product.target.read`; não havia grants de ações de computador para esse target.
- O target `notebook ordax` estava offline (último contato 2026-10-23T04:43:51.376Z), mas a resposta atual expunha o perfil `full-computer-control` com capacidades explícitas de leitura e escrita de computador. Isso corrige a fotografia das 16:40Z, que dizia que o notebook também não tinha grants; não prova que o Runtime local esteja conectado, que uma solicitação possa ser entregue ou que uma ação tenha executado.
- `Meu OrdaX` permanecia offline (último contato 2026-10-21T06:56:03.192Z) e expunha apenas descoberta `product.target.read`.
- A issue #127 continua descrevendo a ausência de grants de ação no **Desktop** e segue aberta. Um perfil concedido ao notebook offline não satisfaz esse E2E nem deve ser interpretado como autorização para trocar silenciosamente de target.
- Nenhuma ação MCP, leitura/alteração do dispositivo, concessão de acesso ou tentativa de reconexão foi feita. A presença online do Desktop e os grants remotos do notebook não comprovam uma sessão MCP de execução.
- MCP sob o OrdaX OS continua sem validação de serviço instalado, ABI/dependências fechadas, supervisor real ou execução física; esta consulta Platform é evidência de catálogo/presença/grants, não integração Native.
- No momento deste snapshot, o remoto canônico estava em `main` `2e2d58d4d622d026d7c8ed61598addb2be2ace9a`, sem PRs abertas e com apenas a branch `main`. Permaneciam abertas #7, #49, #61, #67, #127, #149 e #184.

### Handoff do MCP Native para o owner do OrdaX OS — 2026-10-24 19:36Z

- A leitura da issue OS [#1985](https://github.com/ordaxsystems/ordax-os/issues/1985) confirma que a autorização local e o Action Gateway do OrdaX OS pertencem ao broker de capabilities nativo compartilhado pelos apps. A issue aponta `capability-reader.mjs`, `device-action-envelope-v2.mjs` e `device-action-result.mjs`; também diz para não implantar outro MCP nem copiar o Runtime Windows para o OS. Platform #212 trata do Product MCP remoto e seus grants OAuth, não da autorização local do app.
- O candidato Native do Runtime reutiliza `ActionRegistry` e a identidade do OS no source composition. Os testes/composição Linux não provam que esse processo esteja empacotado, carregado pelo supervisor real, conectado ao Action Gateway ou executando sob as permissões locais do OS.
- O documento [`NATIVE-BUILDROOT-COMPATIBILITY.md`](NATIVE-BUILDROOT-COMPATIBILITY.md) registra que a ABI OS verificada é CPython 3.14, enquanto o lock do agente fixa Python 3.12, além de dependências nativas ausentes ou com versões divergentes. A closure Buildroot, ABI e lifecycle instalado continuam sem prova; não ajustar versões por suposição nem criar supervisor, broker ou transporte paralelo no Runtime.
- **Handoff OS:** concluir/confirmar no contrato canônico da issue #1985 a interface versionada entre supervisor, broker e ação Native: ABI/runtime suportados, origem e binding da identidade, ciclo de start/stop/health e envelope/receipt terminal (incluindo falha e recuperação de request ID). Entregar os pins/receitas Buildroot verificáveis para a closure compatível. Com esse contrato e uma imagem de teste, o Runtime pode validar/adaptar seu dispatcher existente; até lá, não declarar MCP OS operacional.
- **Handoff Platform:** manter grants Product remotos, sessão e dispatch externo sob sua autoridade (#212); metadados de `ordax_targets` não substituem a autorização local do broker OS.
- Snapshot somente de leitura de `ordax_targets` às 19:36:33.443Z: desktop online com `product.target.read` apenas; notebook offline com perfil remoto `full-computer-control`; `Meu OrdaX` offline com descoberta apenas. Nenhuma ação foi chamada. O grant do notebook não valida conectividade, entrega ou execução.
- No mesmo checkpoint, Runtime `main` estava em `c7618645d0729806c77c8a6f0035e644fc9bfdf8`, sem PRs abertas e com somente `main` remota/local. Permanecem abertas #7, #49, #61, #67, #127, #149 e #184.

### Correção do parser de números JSON MCP — 2026-10-24 20:32Z

- Reconciliação GitHub confirmou o repositório `ordaxsystems/ordax-runtime`, `main` em `b21efaba8c7959222fc4bbe0bd415b3d845d06c3`, zero PRs abertas e somente `main` remota antes desta correção. O fetch Git local falhou por indisponibilidade de rede do ambiente; o estado canônico foi conferido pela API GitHub e coincide com o clone local.
- Reprodução isolada com Python 3.12.14 confirmou que `json.loads('{"value":1e9999}')` produz `float('inf')`. O parser Product MCP já rejeitava as constantes não padronizadas `NaN`/`Infinity`, mas não rejeitava esse expoente JSON sintaticamente válido que transborda para um float não finito.
- A mudança candidata adiciona um conversor `parse_float` finito ao parser compartilhado de respostas, regressão ao contrato Product action e atualização de `PRODUCT-ACTION-RECOVERY.md`. A resposta é rejeitada pelo mesmo caminho sanitizado de JSON inválido; não se converte overflow em `null`, valor de sucesso ou autorização.
- `py_compile` dos dois módulos alterados passou e um exercício isolado do conversor confirmou aceitação de `1.25` e rejeição de `1e9999`. A suíte `tests.test_product_action_recovery` não pôde importar porque o Python empacotado não tem `httpx`; essa falha ambiental não é resultado do teste de código. A validação hospedada na PR deve cobrir a regressão antes de qualquer merge.
- Isto fecha apenas a validação de floats finitos no transporte Product MCP. A issue #7 segue aberta: o Runtime ainda não possui o binding autenticado completo do Studio Runtime v3; nenhum request, identidade, resultado ou grant foi fabricado. Não houve sessão MCP de serviço, ação remota, validação física, nem teste sob o supervisor OrdaX OS.

### Classificação do timeout no prazo de espera MCP — 2026-10-24 21:33Z

- A reconciliação da API GitHub confirmou o Runtime canônico em 2ebd04ffc82513e2b288f762f494a4b4672b7d31, sem PRs abertas e somente com a branch main remota. O checkout local independente aponta para o origin canônico e estava limpo antes desta mudança. Permanecem abertas #7, #49, #61, #67, #127, #149 e #184.
- A inspeção mostrou que _request traduzia todo httpx.HTTPError como product_remote_transport_error, inclusive quando um timeout HTTP esgotava o prazo monotônico de wait_action. Como wait_action só converte product_action_wait_timeout para a exceção tipada, o prazo expirado era apresentado como erro genérico de transporte, embora o request_id fosse mantido.
- A alteração candidata classifica como product_action_wait_timeout apenas um erro que ocorre no ou depois do prazo; falhas de transporte anteriores continuam distinguíveis. Duas regressões cobrem ambos os caminhos e a preservação do request_id. O contrato Product action recovery documenta a diferença.
- Validação local: py_compile passou no Python 3.12.14 e uma simulação isolada com transporte falso confirmou os dois caminhos; a simulação não substitui a suíte HTTP real. tests.test_product_action_recovery não importou localmente porque falta httpx (ModuleNotFoundError), uma limitação ambiental, não uma falha de teste de código.
- Na implementação c70aa5c, a CI hospedada passou: ORDAX Runtime Contracts (run 40135477631; o job runtime-contracts executou test_product_action_recovery), Native Buildroot Source Compatibility (40135477697), Native Product Runtime Candidate (40135477686) e Windows Product Build (40135477681, incluindo packaging-contracts, build-installer e install-smoke). Governança de release, atestação Owner/Dev, assinatura, smoke assinado e publicação ficaram skipped.
- A atualização deste registro é documental; a PR #262 precisa manter os checks verdes no head final antes do merge. Esses resultados não comprovam sessão Platform ativa, entrega de ação, recuperação física do serviço Windows, integração ao supervisor Native ou execução em dispositivo real. Nenhuma ação foi enviada e nenhum outro repositório OrdaX foi alterado.

### Redação do token interno de processos persistentes — 2026-10-24 23:34Z

- Reconciliação da API GitHub: main de ordaxsystems/ordax-runtime está em 005a1b137f6c53970917bac4d03d89379bbc12d7, igual ao checkout limpo; não há PRs abertas e a única branch remota é main. O git fetch direto continua indisponível por bloqueio de rede do ambiente; a fotografia canônica foi verificada pela API.
- A inspeção encontrou que o estado persistido inclui token, prova interna de propriedade do supervisor. _public_process_state copiava todo o estado para resultados de start/status/list e das ações computer.*; o caminho de falha em Popen retornava o estado bruto. Isso expunha o token ao resultado da ação/MCP embora o campo só fosse necessário ao Runtime para validar o supervisor.
- A correção remove token da projeção pública e passa a falha de inicialização pelo mesmo sanitizador, preservando o token no arquivo de estado local para ownership/recovery. Regressões cobrem estado público, start com falha, start/status/list de processo em execução.
- Validação local: os quatro testes de tests/test_process_lifecycle_public_state passaram; py_compile dos três módulos/testes alterados e git diff --check passaram. Os testes de integração tests.test_persistent_process_actions foram tentados, mas o import parou por ausência de httpx no Python empacotado (ModuleNotFoundError); não é resultado de teste de código. CI hospedada ainda é necessária para a regressão de falha/start e os contratos Windows.
- Não foram feitas chamadas ao Product MCP de serviço, ações em dispositivo, instalação física, alteração de grants ou edição de ordax-os, ordax-apps ou ordax-platform. A execução nativa sob o supervisor OS segue sem prova física.

### Resultado pós-PR #263 — redação do token do supervisor — 2026-10-24

- PR #263 foi integrada por squash em `main` no commit `496bf9cebe1365cfcdbfb33a1cfe843408dda030`. A branch da PR foi removida depois de confirmar que sua árvore local correspondia ao conteúdo integrado. O checkout isolado foi atualizado para o mesmo SHA.
- No head de código `c52612f2f448b2a6aab0aaed267dda7a18ac30ef`, passaram ORDAX Runtime Contracts (run `40143826868`; o job de contratos registrou 346 testes), Native Runtime Composition, Product MCP Minimum SDK, Native Product Runtime Candidate (run `40143826850`), Native Buildroot Source Compatibility (run `40143826864`) e Windows Product Build (run `40143826855`, com packaging-contracts, build-installer e install-smoke). Governança de release, atestação Owner/Dev, assinatura Authenticode, smoke de bytes assinados e publicação ficaram skipped.
- Localmente passaram os quatro testes de `tests.test_process_lifecycle_public_state`, compilação sintática dos arquivos alterados e `git diff --check`. A suíte `tests.test_persistent_process_actions` não importou no Python empacotado por ausência de `httpx`; o job completo de contratos hospedado passou, cobrindo a regressão.
- Após o merge, API GitHub e checkout reconciliados confirmaram zero PRs abertas e somente `main` remota/local em `496bf9c`.
- A CI de build/install-smoke usa runner descartável; não representa sessão Product MCP autenticada, execução autorizada no computador do proprietário, instalação física Windows ou execução no supervisor real do OrdaX OS. Não houve alteração de grants ou dos repositórios OS, Apps e Platform. Continuam abertas as issues Runtime #7, #49, #61, #67, #127, #149 e #184 nos gates/owners descritos acima.

### Porta real de saúde no watchdog Windows — 2026-10-25

- Reconciliação pela API GitHub: o repositório canônico continua em `main` (`062ff57067cdf174ae601624ffde2ae01d62151a`), sem PRs abertas e com apenas `main` remota. O clone isolado estava limpo antes desta branch. Permanecem abertas as issues #7, #49, #61, #67, #127, #149 e #184; nenhuma evidência nova remove os gates de OS/Platform, sessão Product MCP, grants, assinatura ou dispositivo físico.
- A API de composição `run(..., status_port=0)` permite que o host obtenha uma porta de loopback livre; `start_status_server` publica a porta efetiva em `server_port`. Porém `_start_local_watchdog` não recebia essa porta e o script Windows sondava permanentemente `127.0.0.1:8765`. Em composição Windows com porta dinâmica, o supervisor tratava o Runtime saudável como falha de health e podia disparar recuperação indevida.
- A alteração passa a porta efetiva do listener ao watchdog, valida o intervalo e conserva `8765` como default compatível para invocações manuais do script. O listener continua sendo o serviço existente; nenhum serviço, política ou autoridade nova foi introduzido.
- Validação local: `py_compile`, `git diff --check`, os testes focados de contrato do watchdog e auto-recuperação (2 testes), e um harness com `_start_local_watchdog` simulado em Windows confirmaram o encaminhamento da porta selecionada. Os testes `test_ordax_dev_agent` e `test_native_runtime_composition` não importaram localmente porque o Python empacotado não contém `httpx`; não são falhas de asserção. A PR deve provar esses contratos em CI com as dependências do projeto.
- Uma execução suplementar dos testes de status e auto-recuperação registrou 20 aprovações, uma falha e um erro em asserções antigas, não relacionadas à mudança: uma ainda exige `--control-plane-url` removido pelo transporte Product canônico e outra procura a chamada antiga `start_status_server(status_payload)` sem os parâmetros atuais. Esses arquivos não foram alterados nesta branch; os contratos específicos do watchdog passaram.
- No head de código `5ea6f8d9aa55b82627a5d835e48d27dc4742ce9d` da PR #265 passaram ORDAX Runtime Contracts (run `40147586679`, incluindo `test_native_runtime_composition`), Native Product Runtime Candidate (`40147586542`), Native Buildroot Source Compatibility (`40147586541`) e Windows Product Build (`40147586609`, packaging-contracts, build-installer e install-smoke). Governança, atestação Owner/Dev, assinatura, smoke assinado e publicação ficaram skipped; nenhum release assinado foi produzido. Esta atualização documental exige que os checks sejam repetidos no head final antes do merge.
- Os runners hospedados comprovam as suites de fonte, o candidato Native offline e o install-smoke descartável; não validam instalação física Windows com listener dinâmico, sessão MCP Product autenticada nem execução Native sob supervisor real do OrdaX OS. Nenhuma chamada de ação foi enviada; grants e os repositórios OS, Apps e Platform não foram alterados.
