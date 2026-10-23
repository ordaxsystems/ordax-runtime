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

## Execute the actual OS-private interpreter with the canonical agent source

After independently authenticating an `ordax-os` Buildroot Private Python
artifact, extract its **non-installable** target TAR only into a disposable
Linux CI sandbox. The exact TAR must be passed back to the canonical
read-only reconciler; the extracted tree supplies executable bytes, not a
new runtime owner.

```sh
python3 -m tools.ci.probe_native_buildroot_agent \
  --receipt /verified/native-python-provenance.json \
  --private-target-tar /verified/native-python-build-input.rootfs.tar \
  --extracted-private-root /sandbox/verified-target
```

This runs the **existing** `ordax_dev_agent.native_main.self_test()` and
constructs the canonical Product HTTP client using the private Buildroot
CPython/musl interpreter and this checkout's source. Device credentials
and remote network calls are prohibited; test process/file operations use
the disposable *host* Linux environment, so the result is **not** a QEMU,
signed component or physical device proof. Missing external imports are
reported as `native_missing_import` plus the first module name.
Exit code **0** means only offline source bootstrap worked; **1** means
bootstrap did not work; **2** means evidence/input validation failed.
Every result keeps `device_install_authorized=false` and
`full_native_runtime_dependency_closure_proven=false` even on success.
Never run this probe with a privileged OS root or on a production notebook.

## Verified Buildroot-private Python versus Runtime lock

The OS-owned experimental Independent Base job [39736013343](https://github.com/ordaxsystems/ordax-os/actions/runs/39736013343)
compiled a component-private musl **Python 3.14.7**, TLS, zlib and an HTTPX
subset using the pinned Buildroot 2026.08 source. It executed a read-only
offline host-kernel smoke. Its actual `native-python-provenance.json` is the
**source of measured Buildroot versions**, not a new package lock in this repo.
It is intentionally a non-installable component, separate from the OS rootfs.

The canonical Runtime uses a Python **3.12** hashed lock. Against the above
verified OS receipt, the Buildroot probe disagrees on `anyio`
(4.15.1 vs 4.13.0), `certifi` (2026.10.19 vs 2026.4.22) and `idna`
(3.20 vs 3.11); `httpx`, `httpcore` and `h11` agree. The other
Runtime lock packages have **no closure proof from that six-package probe**.

Reconcile an externally verified OS artifact with the exact OS source
contract using the existing Runtime lock parser:

```sh
python3 -m tools.ci.audit_independent_python_proof \
  --receipt /verified/native-python-provenance.json \
  --os-source /pinned/ordax-os/bootstrap/independent-base/source.json \
  --requirements native/ordax-product-agent/requirements.lock \
  --require-alignment
```

The current verified proof should return **1** for differences, print exact
Buildroot/Runtime versions and report uncovered locks. Malformed/unbound
evidence returns **2**. The output binds SHA-256 of both inputs and is a
read-only *version reconciliation*, not a signature verification or permission
to vendor other-distribution libraries. The OS owner verifies workflow
provenance and actual ELF closure; this tool does not. No combination of
matching versions may turn on `device_install_authorized`,
`device_execution_proven` or `full_native_runtime_dependency_closure_proven`.
The next compatible native build must use OS-owned Buildroot inputs and
verify the complete Runtime import graph and kernel execution.

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

The same auditor also walks the actual PyInstaller CArchive table and examines
every frozen native `b` entry with the **existing** ELF parser, bounded
decompression and strict path/size checks. `candidate.json` now binds
`embedded_elf_inventory`: member count, total unpacked native bytes,
aggregated exact member-hash fingerprint, declared library requirements and
names not supplied by frozen members. This is a **diagnostic inventory**, not
proof that the target loader can resolve names, execute extensions or satisfy
Python's import-time ABI. The same CArchive cookie is now checked for an exact
embedded Python major/minor version and matching `libpython` shared-library
name; `embedded_python_version` and `embedded_python_library` are reported in
the existing `embedded_elf_inventory` manifest section. Inconsistent or
malformed cookie metadata fails closed. A match only identifies the Python
runtime bundled into this executable; it does **not** imply the host Buildroot
Python (if any) must have the same minor version, nor that the frozen interpreter
or extensions can load against the target libc. No new ABI authority is created. The preceding upstream artifact contained 90
frozen ELF members; its native dependency names included unresolved
`libc.musl-x86_64.so.1` and `libc.so` when compared with frozen member
basenames. That finding cannot be fixed by copying Alpine's libc into the OS.

### Read-only target rootfs dependency proof

The **same canonical native ABI auditor** can inspect the exact Buildroot
`rootfs.tar` produced by `ordax-os/bootstrap/independent-base/build.py`
without unpacking into the host filesystem or executing any package.
Supply the OS-verified tarball to the Runtime-owned parser:

```sh
python3 native/ordax-product-agent/audit_elf.py \
  --binary out/ordax-product-agent \
  --recipe native/ordax-product-agent/Dockerfile \
  --rootfs-tar /verified/ordax-independent/rootfs.tar \
  --require-rootfs-name-resolution
```

The actual Owner/Development QEMU artifact publishes
`ordax-independent/rootfs.cpio.gz`, **not** `rootfs.tar`. The same canonical
auditor supports that exact published artifact without extracting files:

```sh
python3 native/ordax-product-agent/audit_elf.py \
  --binary out/ordax-product-agent \
  --recipe native/ordax-product-agent/Dockerfile \
  --rootfs-cpio /verified/ordax-independent/rootfs.cpio.gz \
  --expected-rootfs-sha256 "$OS_AUTHENTICATED_ROOTFS_SHA256" \
  --require-rootfs-name-resolution
```

The CPIO adapter parses only Buildroot's `newc` format, bounds total inflated
bytes and file counts, discards firmware and other irrelevant OS files without
extracting or staging them, and reuses the **same** ELF parser, symlink resolution
and dependency graph. TAR and CPIO preflight hash and parse through the
**same open regular-file descriptor**; a pathname replacement cannot change
the parsed bytes, and detected in-place mutations fail closed.
The report binds the SHA-256 of the **exact compressed
CPIO** in `target_rootfs_cpio_sha256`; the TAR option retains
`target_rootfs_tar_sha256`. Supply a separately authenticated GitHub/OS rootfs
artifact: this consumer cannot authenticate its producer. The optional
`--expected-rootfs-sha256` rejects mismatched TAR/CPIO source bytes (and
missing/invalid SHA-256 inputs); derive the digest from the **authenticated OS
`provenance.json`**, not from unauthenticated archive bytes. This identity
binding is not full Runtime ABI proof or installation authorization.

The report binds the **actual source archive SHA-256**, inspects the target loader and
ELF files in standard `/lib{,64}` and `/usr/lib{,64}` namespaces, follows
in-archive symlinks without extraction, checks transitive `DT_NEEDED` names,
and identifies missing or ambiguous libraries. It fails closed for truncated,
malformed, unsafe or ambiguous library entries. The strict CLI option returns
nonzero for missing names/loader, but a green result **still does not authorize
installation**: SONAME path availability cannot establish the Python minor ABI,
loader search policy, symbol versioning, `dlopen` imports, frozen-module
relocations, reproducibility or actual QEMU/physical execution.
`full_runtime_abi_compatible`, `native_dependency_closure_proven` and
`device_install_authorized` explicitly remain false. This does not create
another rootfs owner, compiler, installer or device-side executor.

The current minimal Independent Base is static musl, so the Alpine-built
dynamic candidate is expected to fail this strict rootfs preflight.
The Buildroot owner (#1860 in `ordax-os`) must decide whether to produce
a truly static/self-contained native Runtime or move to a reproducible
Buildroot-owned dynamic userspace, then prove the exact artifact's complete
closure on QEMU. No Alpine APK, loader symlink or foreign distro is a solution.

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


### Local OS Connector consent probe (read-only)

The frozen `ordax-product-agent` also accepts
`--local-access-status --distribution-profile owner-development --product-mode usb`
with the existing `--system-root`, `--state-root` and `--workspace` paths.
It imports **the OS-owned** `ProductDeviceIdentityStore` source through the
same verified Native composition used by `--run` and checks
`development_visibility_enabled()`. Status prints only
`NATIVE_PRODUCT_LOCAL_ACCESS=ENABLED` (exit 0) or `DISABLED` (exit 3);
unknown, absent credential and unsafe settings deny. It performs no network
calls, enrollments, state writes, singleton-lock acquisition, OAuth/grant
lookup or action dispatch. `--full-access` is forbidden in probe mode.

This is a lifecycle *predicate*, not a session attestation, readiness
receipt or activation authority. The OS supervisor must still use its
canonical signed-current-slot verification before execution, run the
same executable with `--run`, and stop it if local consent is revoked.
Do not parse `product-connector-visibility.json` again in the supervisor.
