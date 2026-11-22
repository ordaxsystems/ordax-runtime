FROM python:3.12.12-alpine3.22@sha256:5c84c59004e6679bc98a33930344c6ea3da9e2434869b29bc950e46902b6c383 AS build

RUN apk add --no-cache gcc=14.2.0-r6 musl-dev=1.2.5-r13 zlib-dev=1.4-r0
WORKDIR /source
COPY native/ordax-product-agent/requirements.lock /build/requirements.lock
COPY native/ordax-product-agent/bootstrap.lock /build/bootstrap.lock
RUN python -m pip install --require-hashes -r /build/bootstrap.lock
ENV PYINSTALLER_COMPILE_BOOTLOADER=1
RUN python -m pip install --no-build-isolation --no-binary=pyinstaller --require-hashes -r /build/requirements.lock
COPY . /source
RUN python -m pip install --no-deps --no-build-isolation . && \
    python -m unittest tests.test_native_product_package && \
    python -m PyInstaller --noconfirm --clean --onefile \
      --name ordax-product-agent --distpath /output --workpath /build/work \
      --specpath /build --paths /source \
      --copy-metadata ordax-runtime native/ordax-product-agent/entrypoint.py
# Ship the dynamic loader and zlib from this EXACT toolchain alongside the
# frozen interpreter. Never ask the OS rootfs to provide either library.
# libc.musl is a byte-identical loader alias for bundled extension modules.
RUN mkdir -p /output/portable-lib && \
    cp -L /lib/ld-musl-x86_64.so.1 /output/portable-lib/ld-musl-x86_64.so.1 && \
    cp -L /lib/ld-musl-x86_64.so.1 /output/portable-lib/libc.musl-x86_64.so.1 && \
    cp -L /usr/lib/libz.so.1 /output/portable-lib/libz.so.1 && \
    chmod 755 /output/portable-lib/ld-musl-x86_64.so.1

# Kernel-only boot probe: no Alpine, no libc, no /bin, no external
# interpreter in the test image. This is a BOOTSTRAP probe, NOT proof of
# an installed Runtime, signed activation or full Python dependency closure.
FROM scratch AS kernel-only-smoke
COPY --from=build /output/ordax-product-agent /output/ordax-product-agent
COPY --from=build /output/portable-lib/ /output/portable-lib/
ENV LD_LIBRARY_PATH=/output/portable-lib
ENTRYPOINT ["/output/portable-lib/ld-musl-x86_64.so.1", "--library-path", "/output/portable-lib", "/output/ordax-product-agent"]

FROM alpine:3.22.5@sha256:7c8cb692ae09657cbc4a3f3cbd0e8d5a2690ba38386aaaf252dbb060bf5eb2e6
RUN apk add --no-cache zlib=1.3.2-r0
COPY --from=build /output/ordax-product-agent /output/ordax-product-agent
COPY --from=build /output/portable-lib/ /output/portable-lib/
ENTRYPOINT ["/output/ordax-product-agent"]
