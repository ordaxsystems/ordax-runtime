# OrdaX Owner Linux Recovery Agent

This directory owns the **portable Linux transport** for the internal
`owner-development` USB. It reuses the existing Cloudflare v3 Device Agent
protocol but intentionally exposes only a tiny typed recovery surface.

It is not a second Device Agent and it is not a Stable/MVP component.

Allowed remote capabilities are exactly:

- `agent.owner.snapshot` — fixed-path bounded diagnostic snapshot;
- `agent.owner.git_status` — read-only status of `/workspace/ordax`;
- `agent.owner.pull` — exact `/usr/local/bin/ordax-pull`, no arguments;
- `agent.owner.runtime_stop` — TERM only after guardian PID/cmdline ownership proof;
- `agent.owner.runtime_start` — exact `/usr/local/bin/ordax-run`, no arguments.

No generic command, remote path, shell, package installation, Git push, block-device
access, release signing or arbitrary process control is accepted. Every job binds
the existing job/effect/attempt/lease/epoch/agent/boot execution context and
verifies the canonical payload digest before execution. Mutating effects are
journaled locally before execution and replay is fail-closed.

Credentials are device-scoped state and must never be committed:

- `/state/ordax/owner-remote/control-plane-url`
- `/state/ordax/owner-remote/device-id`
- `/state/ordax/owner-remote/device-token` (`0600`)

The OS owns activation and artifact pinning. An unpaired or unavailable remote
channel must never block local boot, Git updates or the Surface.
