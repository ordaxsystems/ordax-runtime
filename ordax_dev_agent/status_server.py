from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .capability_snapshot import device_capability_snapshot


_DASHBOARD_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OrdaX Dev Agent</title>
<style>
:root {
  color-scheme: dark;
  font-family: Inter, Segoe UI, system-ui, sans-serif;
  background: #071019;
  color: #e9f4ff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(circle at 20% 0%, rgba(0,165,255,.16), transparent 38rem),
    linear-gradient(180deg, #071019 0%, #09141f 100%);
}
main { max-width: 1180px; margin: 0 auto; padding: 42px 28px 56px; }
header { display:flex; align-items:flex-end; justify-content:space-between; gap:24px; margin-bottom:28px; }
h1 { margin:0; font-size:30px; letter-spacing:-.03em; }
.subtitle { margin-top:7px; color:#8ba7bd; font-size:14px; }
.pill {
  display:inline-flex; align-items:center; gap:8px; padding:8px 12px;
  border:1px solid #20384b; border-radius:999px; background:#0c1a26;
  color:#aac0d1; font-size:13px;
}
.dot { width:9px; height:9px; border-radius:50%; background:#728494; box-shadow:0 0 0 3px rgba(114,132,148,.12); }
.dot.ok { background:#35d07f; box-shadow:0 0 0 3px rgba(53,208,127,.12); }
.dot.busy { background:#ffbe45; box-shadow:0 0 0 3px rgba(255,190,69,.12); }
.dot.bad { background:#ff5f68; box-shadow:0 0 0 3px rgba(255,95,104,.12); }
.grid { display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:16px; }
.card {
  grid-column:span 4; min-height:150px; border:1px solid #183043;
  background:rgba(10,25,37,.88); border-radius:16px; padding:20px;
  box-shadow:0 14px 35px rgba(0,0,0,.18);
}
.card.wide { grid-column:span 8; }
.card.full { grid-column:span 12; min-height:0; }
.label { color:#7895aa; font-size:12px; text-transform:uppercase; letter-spacing:.11em; }
.value { margin-top:10px; font-size:22px; font-weight:650; word-break:break-word; }
.meta { margin-top:9px; color:#9ab0c0; font-size:13px; line-height:1.55; }
.actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
.action {
  padding:6px 9px; border-radius:8px; background:#102535; color:#9fc6df;
  border:1px solid #1e4057; font:12px ui-monospace,SFMono-Regular,Consolas,monospace;
}
.result {
  margin-top:12px; padding:13px 14px; border-radius:10px; background:#07131d;
  border:1px solid #142b3b; font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;
  white-space:pre-wrap; overflow-wrap:anywhere; min-height:54px;
}
footer { margin-top:22px; color:#658196; font-size:12px; }
@media(max-width:820px) {
  header { align-items:flex-start; flex-direction:column; }
  .card,.card.wide { grid-column:span 12; }
}
</style>
</head>
<body>
<main>
<header>
  <div>
    <h1>OrdaX Dev Agent</h1>
    <div class="subtitle">Unity · Blender · Git · OrdaX Control Plane</div>
  </div>
  <div class="pill"><span id="state-dot" class="dot"></span><span id="state">carregando…</span></div>
</header>

<section class="grid">
  <article class="card">
    <div class="label">Agente</div>
    <div id="agent" class="value">—</div>
    <div id="agent-meta" class="meta">—</div>
  </article>

  <article class="card">
    <div class="label">Control Plane</div>
    <div id="control-plane" class="value">—</div>
    <div id="pairing" class="meta">—</div>
  </article>

  <article class="card">
    <div class="label">Último job</div>
    <div id="job" class="value">—</div>
    <div id="job-meta" class="meta">—</div>
    <div id="job-phase" class="meta"></div>
    <div id="job-timings" class="meta"></div>
  </article>

  <article class="card wide">
    <div class="label">Último resultado</div>
    <div id="result" class="result">Nenhum job executado nesta sessão.</div>
  </article>

  <article class="card">
    <div class="label">Unity ao vivo</div>
    <div id="unity-live" class="value">—</div>
    <div id="unity-live-meta" class="meta">—</div>
  </article>

  <article class="card">
    <div class="label">Blender ao vivo</div>
    <div id="blender-live" class="value">—</div>
    <div id="blender-live-meta" class="meta">—</div>
  </article>

  <article class="card">
    <div class="label">Projetos locais</div>
    <div id="projects" class="meta">—</div>
    <div id="progress" class="meta"></div>
  </article>

  <article class="card full">
    <div class="label">Capacidades permitidas</div>
    <div id="actions" class="actions"></div>
  </article>
</section>

<footer>
  Somente localhost · status atualizado automaticamente · nenhum shell remoto arbitrário.
</footer>
</main>
<script>
function stateClass(state) {
  if (state === 'ready' || state === 'local-ready') return 'ok';
  if (state === 'busy' || state === 'pairing' || state === 'restarting') return 'busy';
  if (state && state.includes('error')) return 'bad';
  return '';
}
async function refresh() {
  try {
    const response = await fetch('/status', {cache:'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const s = await response.json();
    const r = s.runtime || {};
    document.getElementById('agent').textContent = s.agent_name || '—';
    document.getElementById('agent-meta').textContent =
      'v' + (s.agent_version || '?') + ' · poll ' + (s.poll_seconds || '?') + 's';
    document.getElementById('control-plane').textContent =
      s.control_plane_protocol || 'não configurado';
    document.getElementById('pairing').textContent =
      r.last_heartbeat_at
        ? 'Último heartbeat há ' + Math.max(0, Math.floor(Date.now()/1000-r.last_heartbeat_at)) + 's'
        : 'Aguardando confirmação do Control Plane';
    document.getElementById('job').textContent = r.last_job_action || 'Nenhum';
    document.getElementById('job-meta').textContent =
      r.last_job_id ? r.last_job_id : 'Sem job nesta sessão';
    document.getElementById('result').textContent =
      r.last_result ? JSON.stringify(r.last_result, null, 2) : 'Nenhum job executado nesta sessão.';
    const phases = {idle:'Aguardando trabalho', executing:'Executando', uploading:'Enviando arquivos',
      reporting:'Confirmando resultado', completed:'Concluído', failed:'Falhou', 'delivery-error':'Falha de comunicação'};
    document.getElementById('job-phase').textContent = phases[r.job_phase] || '';
    const timings = r.job_timings || {};
    document.getElementById('job-timings').textContent =
      Object.entries(timings).map(([key, value]) =>
        ({execution_seconds:'Execução', upload_seconds:'Envio', report_seconds:'Confirmação', total_seconds:'Total'}[key] || key)
        + ': ' + value + 's').join(' · ');
    const live = s.live_apps || {};
    const defaultProject = s.default_project || Object.keys(live)[0];
    const appLive = defaultProject ? (live[defaultProject] || {}) : {};

    const unity = appLive.unity || {};
    const unityPresence = unity.presence || {};
    document.getElementById('unity-live').textContent =
      unity.presence_fresh ? 'Aberto' : 'Fechado / aguardando';
    document.getElementById('unity-live-meta').textContent =
      unity.presence_fresh
        ? ((unityPresence.playing ? 'Play Mode' : 'Editor') +
           (unityPresence.compiling ? ' · compilando' : ' · pronto') +
           (unityPresence.unityVersion ? ' · ' + unityPresence.unityVersion : ''))
        : (unity.error || 'Companion sem heartbeat');

    const blender = appLive.blender || {};
    const blenderPresence = blender.presence || {};
    document.getElementById('blender-live').textContent =
      blender.presence_fresh ? 'Aberto' : 'Fechado / aguardando';
    document.getElementById('blender-live-meta').textContent =
      blender.presence_fresh
        ? ((blenderPresence.scene || 'Scene') +
           ' · ' + (blenderPresence.objects ?? 0) + ' objetos' +
           (blenderPresence.is_dirty ? ' · alterações não salvas' : ' · sincronizado'))
        : (blender.error || 'Companion sem heartbeat');

    const projects = s.projects || [];
    document.getElementById('projects').replaceChildren(...projects.map(project => {
      const row = document.createElement('div');
      row.textContent = project.slug + ' · ' + (project.apps || []).join(', ') +
        (project.available ? ' · disponível' : ' · caminho ausente');
      row.title = project.path;
      return row;
    }));
    document.getElementById('progress').textContent = r.progress ?
      'Captura ' + (r.progress.index + 1) + ' · ' + r.progress.duration_seconds + 's' +
      (r.progress.delivery_error ? ' · falha na entrega' : '') : '';
    const actions = document.getElementById('actions');
    actions.replaceChildren(...(s.actions || []).map(name => {
      const el = document.createElement('span');
      el.className = 'action';
      el.textContent = name;
      return el;
    }));
    const state = r.state || 'unknown';
    document.getElementById('state').textContent = state;
    document.getElementById('state-dot').className = 'dot ' + stateClass(state);
  } catch (error) {
    document.getElementById('state').textContent = 'offline';
    document.getElementById('state-dot').className = 'dot bad';
  }
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


def start_status_server(
    status_provider: Callable[[], dict],
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    # This service returns device/project paths and Runtime metadata. Never
    # expose it on an external interface, even when explicitly misconfigured.
    # In particular, loopback binding alone does not stop DNS rebinding:
    # a hostile webpage may resolve its own Host name to 127.0.0.1.
    if host != "127.0.0.1":
        raise ValueError("ORDAX status server must bind to 127.0.0.1")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("ORDAX status server port is invalid")

    class Handler(BaseHTTPRequestHandler):
        def _deny(self) -> None:
            self.send_response(403)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def _trusted_loopback_request(self) -> bool:
            # Require one literal loopback Host + the *actual listening port*.
            # No aliases from DNS, origin URLs, arbitrary ports or userinfo.
            # A literal IPv4/localhost Host is not a credential: this gate
            # prevents browser DNS-rebinding reads, not privileged actions.
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or self.client_address[0] != "127.0.0.1":
                return False
            authority = hosts[0].lower()
            port = self.server.server_port
            return authority in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _headers(self, content_type: str, length: int) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            )
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._trusted_loopback_request():
                self._deny()
                return
            path = self.path.split("?", 1)[0]

            if path == "/":
                body = _DASHBOARD_HTML.encode("utf-8")
                self._headers("text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return

            if path == "/health":
                status = status_provider()
                runtime = status.get("runtime") or {}
                body = json.dumps(
                    {
                        "ok": runtime.get("state") not in {
                            "pairing-error",
                            "control-plane-error",
                        },
                        "state": runtime.get("state"),
                        "version": status.get("agent_version"),
                    }
                ).encode("utf-8")
                self._headers("application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return

            if path == "/capabilities":
                # The shared request gate covers *every* path, including
                # public health and private status/capability metadata.
                status = status_provider()
                actions = status.get("actions") if isinstance(status, dict) else []
                snapshot = device_capability_snapshot(
                    actions if isinstance(actions, list) else []
                )
                body = json.dumps(snapshot, separators=(",", ":")).encode("utf-8")
                self._headers("application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return

            if path == "/status":
                body = json.dumps(status_provider(), indent=2).encode("utf-8")
                self._headers("application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return

            self.send_response(404)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="ordax-agent-status",
        daemon=True,
    )
    thread.start()
    return server
