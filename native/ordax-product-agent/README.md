# Native Product Runtime candidate

This recipe builds the official Python executor into an amd64/musl executable.
The device needs its native kernel/userspace ABI, not a Python installation or
an external Linux desktop. The executable reuses the Product transport,
attestation, dispatch, leases and outbox; it is separate from the narrow Owner
Recovery agent and does not extend that recovery protocol.

Build with Docker from the repository root:

```
docker build --platform linux/amd64 -f native/ordax-product-agent/Dockerfile -t ordax-native-product-candidate .
docker run --rm --network none --read-only --tmpfs /tmp:exec ordax-native-product-candidate --self-test
```

The current recipe **still uses Alpine only as a build/proof container**,
not as OrdaX OS userspace. Pinning its image and dependencies does **not** make
the resulting ELF independent of Alpine's musl/zlib toolchain. The minimal
container proof image has no Python installation, but the actual executable
contains a frozen Python runtime and may need a Linux dynamic loader and
native shared libraries. The executable probe performs real temporary filesystem,
terminal and supervised-process operations without network or device credentials.
The CI artifact includes executable SHA-256/size, exact source SHA, dependency
lock/recipe hashes and the offline proof. Byte-for-byte reproducibility has not
been established. This is an unsigned candidate, not an installable signed OS
component or evidence of physical notebook execution.

## Cross-repository dependency version audit — required before replacing Alpine

The Buildroot-built private CPython **3.14.7** passes isolated execution with
its musl loader, TLS, and zlib in the OrdaX OS Independent Base branch. This is
**not** proof that the currently locked Product MCP dependencies are compatible:
`native/ordax-product-agent/requirements.lock` was resolved for Python **3.12**.

The canonical upstream Runtime now includes `audit_buildroot_lock.py` to
compare its actual locked package versions against the exact
**SHA256-verified, already extracted Buildroot source** selected by the OS
owner. No duplicated version list, forked package manager or extra runtime.
Run it from this repository on a verified Buildroot tree:

```sh
python3 native/ordax-product-agent/audit_buildroot_lock.py \
  --buildroot /path/to/verified/buildroot-2026.08
```

The JSON lists the Python-ABI mismatch, missing Buildroot packages, and
source-version mismatches. The `--require-compatible` form fails closed
until all source versions match. Both outputs **always** keep
`native_dependency_closure_proven=false`,
`runtime_agent_packaged=false` and `device_install_authorized=false`:
version parity alone never proves cross-compilation or ELF compatibility.

Important verified examples in Buildroot **2026.08**: `python-httpx`
matches the current lock at `0.28.1`, but `python-websockets` is
`16.0` against locked `15.0.1`, `python-pillow` is `12.0.0` against
`11.3.0`, `python-cryptography` is `48.0.0` against `50.0.2`,
`python-pydantic-core` is `2.46.3` against `2.50.1`, and no
`python-mcp` target package exists in that release. The **single**
`ordax-runtime` owner must reconcile these exact pins and provide a
canonical Buildroot recipe for missing packages, then prove wheels/extensions
built by the OrdaX toolchain with the actual Python ABI. Do not copy
Alpine wheels, silently accept newer incompatible versions, introduce
a second Product dispatcher, or enable installation early.

## Independent Base / kernel-only ABI gate

The OrdaX OS Independent Base uses a **Buildroot-built static musl** userspace.
That base does not promise an Alpine loader, APK or a dynamic libz. The
existing Alpine-produced ELF is **not ready for it**. Building and executing
an ELF inside Alpine proves neither kernel-only compatibility nor the
portability of its frozen Python shared-object closure.

The canonical `provenance.py` records `elf_interpreter`,
`elf_needed_libraries`, `independent_base_abi_ready` and
`independent_base_abi_blockers` in the **same candidate.json**. The auditor
parses ELF segments and dynamic tags directly, without running the artifact,
using Alpine or relying on `ldd`. CI prints the report for the actual
compiled executable.

To inspect without authorizing or installing anything:

```sh
python3 native/ordax-product-agent/audit_elf.py \
  --binary out/ordax-product-agent \
  --recipe native/ordax-product-agent/Dockerfile
```

The release-gate form `--require-independent` exits nonzero if the ELF
needs an external interpreter or shared library, the build recipe is Alpine,
or a PyInstaller archive has no audited extracted-library closure. A
candidate's normal CI build may remain green while these **explicit** blockers
exist; `installation_authorized_by_candidate=false` is still mandatory.

Do **not** fix this by adding an Alpine minirootfs, creating a loader symlink,
copying `/lib/ld-musl-...` from an Alpine image or replacing Alpine with a
Debian/glibc runtime. The actual next build must use the canonical
Independent Base toolchain or an equivalently reproducible OrdaX-owned
userspace ABI, prove the **whole** bundled interpreter/native-module closure,
and pass the strict gate before any signing/install/health proof. The
Independent Base integration remains owned by `ordax-os#1860`; this repo
owns the single Runtime implementation and its candidate provenance.

The OS must supervise a single instance and replace its discovery-only presence
owner before activation. A future verified installation may invoke:

```
ordax-product-agent --run --distribution-profile owner-development --product-mode usb
```

The default paths refer to the host's existing workspace and private native
state. The executable loads `ProductDeviceIdentityStore` from the trusted OS
source and calls its existing reader. It creates no identity, copies no token,
and does not run if visibility is disabled or the credential is absent. The
local instance lock also rejects duplicate executable launches.

`--full-access` explicitly enables the local Owner DEV computer policy for that
process. Product OAuth, device-scoped grants and session attestation still apply.
Stable/native-disk activation is unavailable. Graphical commands remain absent
until the OS supplies native screen/input handlers.

The detached process worker uses the same executable with an internal worker
mode. It starts an independent bundle instance so it can outlive the parent,
and system commands receive the host's library search path. This follows the
PyInstaller runtime guidance; no target-side Python or Bash is required.

To refresh Python locks, use the canonical pyproject plus build-only inputs:

```
uv pip compile pyproject.toml native/ordax-product-agent/build-requirements.in --python-version 3.12 --generate-hashes -o native/ordax-product-agent/requirements.lock
```

Refresh `bootstrap.lock` from the build-only dependency graph with
`--no-emit-package pyinstaller`. It contains hashed build backend/tooling entries,
installed before the compiler-backed PyInstaller build with build isolation
disabled. The remaining Runtime dependencies are installed from the full lock.
