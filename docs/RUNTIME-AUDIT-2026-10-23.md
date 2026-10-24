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
- The old 2026-10-09 description in issue #67 is superseded: Runtime PR #231 integrated the Apps-owned Studio Conversation/Electron presentation into the Windows installer. The current Runtime source lock pins Apps Studio 0.15.9 at `a297b691d5a91ea0370d80f32533931596dfe5f6`. Issue #67 remains open for real provider/preview/MCP-grant, signed-release and physical-device evidence; the installer smoke is runner evidence only.
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

