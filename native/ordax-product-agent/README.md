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

The base images are pinned by digest, direct compiler packages by exact version,
and Python dependencies by version and hashes. The minimal proof image has no
Python installation. The executable probe performs real temporary filesystem,
terminal and supervised-process operations without network or device credentials.
The CI artifact includes executable SHA-256/size, exact source SHA, dependency
lock/recipe hashes and the offline proof. Byte-for-byte reproducibility has not
been established. This is an unsigned candidate, not an installable signed OS
component or evidence of physical notebook execution.

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
