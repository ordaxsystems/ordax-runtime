# OrdaX Desktop Product Runtime component

`windows-desktop-component.yml` produces one `ordax-product-agent.exe` for
`windows-amd64`. It embeds Python and the existing canonical Runtime; it has
no Studio application dependency, installer, enrollment or second agent.
The Windows dependency closure is hash-pinned in `desktop-requirements.lock`.

The executable requires the Supervisor's `ordax-desktop` profile and delegates
to `ordax_dev_agent.desktop_host_main.main`. Its only current action is
`agent.status`. Missing identity and foreign profiles fail closed.

CI tests identity/transport contracts, audits the frozen module inventory,
and executes an isolated copy with no Python or checkout on PATH. The
`--self-test` probe uses disposable state and no enrolled device credential.
This proves local packaging, not a remote MCP session or device grant.

`candidate.json`, `self-test.json` and `SHA256SUMS` bind proof to exact source
and executable bytes. `product-runtime.zip` and `runtime-component-release.json`
use the OS component-channel v1 format, with one Windows executable and pending
health required. They contain no signature or signing key.
These are unsigned CI evidence, never a trusted slot
receipt. The OS owns component signing, trust and activation. Promotion still
requires the official external custody ceremony, public trust promotion,
signed Windows component verification and separate runtime health proof.
No candidate installs itself or alters Desktop userData.
