# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build HiGHS, then Yasol against it.
# ---------------------------------------------------------------------------
FROM debian:bookworm AS builder

# Pinned to commits, not branches: this container produces the ground-truth values of a
# benchmark, so the solver must not change underneath the results.
ARG HIGHS_REF=04024d701f79feb8e2f18bc3df0dffc04ef05088
ARG YASOL_REF=11ae58f7d96c65598c820b31cb68c353e68cc5dd
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Yasol's cmake/modules/FindHighs.cmake expects a HiGHS *source tree*: headers in
# <root>/src or <root>/highs, HConfig.h in <root>/build, libhighs in <root>/build/lib.
# So HiGHS is built in place rather than installed to a prefix.
RUN git init -q /opt/HiGHS \
    && git -C /opt/HiGHS remote add origin https://github.com/ERGO-Code/HiGHS.git \
    && git -C /opt/HiGHS fetch -q --depth 1 origin "${HIGHS_REF}" \
    && git -C /opt/HiGHS checkout -q FETCH_HEAD \
    && cmake -S /opt/HiGHS -B /opt/HiGHS/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=ON \
        -DBUILD_TESTING=OFF \
    && cmake --build /opt/HiGHS/build -j"$(nproc)"

# Yasol's CMakeLists.txt hardcodes -m64, which gcc only accepts on x86. The flag is a
# no-op there (64-bit is the default), so on other architectures it is stripped. The
# sources themselves contain no x86 intrinsics.
RUN git init -q /opt/Yasol \
    && git -C /opt/Yasol remote add origin https://github.com/MichaelHartisch/Yasol.git \
    && git -C /opt/Yasol fetch -q --depth 1 origin "${YASOL_REF}" \
    && git -C /opt/Yasol checkout -q FETCH_HEAD \
    && if [ "${TARGETARCH}" != "amd64" ]; then \
           sed -i 's/ -m64//' /opt/Yasol/CMakeLists.txt; \
       fi \
    && cmake -S /opt/Yasol -B /opt/Yasol/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DUSE_HIGHS=ON \
        -DHIGHS_ROOT_DIR=/opt/HiGHS \
    && cmake --build /opt/Yasol/build -j"$(nproc)"

# Resolved commits travel with the image so a results file can name its solver exactly.
RUN printf 'yasol=%s\nhighs=%s\n' \
        "$(git -C /opt/Yasol rev-parse HEAD)" \
        "$(git -C /opt/HiGHS rev-parse HEAD)" \
    > /opt/solver-provenance.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime — Yasol binary, Julia + YasolSolver.jl, FastAPI service.
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS runtime

ARG JULIA_VERSION=1.9.4
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    YASOL_BIN=/usr/local/bin/yasol \
    JULIA_DEPOT_PATH=/opt/julia-depot \
    JULIA_PROJECT=/opt/julia-env

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgomp1 \
        libstdc++6 \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

# --- Yasol + HiGHS runtime bits -------------------------------------------
COPY --from=builder /opt/Yasol/build/yasol /usr/local/bin/yasol
COPY --from=builder /opt/HiGHS/build/lib/libhighs.so* /usr/local/lib/
COPY --from=builder /opt/Yasol/examples/ /opt/yasol-examples/
COPY --from=builder /opt/solver-provenance.txt /opt/yasol/solver-provenance.txt
RUN ldconfig && yasol --help > /dev/null

# --- Julia ----------------------------------------------------------------
# The official tarballs use x86_64/aarch64; TARGETARCH gives amd64/arm64.
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) JL_ARCH=x64;   JL_DIR=x86_64 ;; \
        arm64) JL_ARCH=aarch64; JL_DIR=aarch64 ;; \
        *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    JL_MINOR="$(echo "${JULIA_VERSION}" | cut -d. -f1,2)"; \
    curl -fsSL "https://julialang-s3.julialang.org/bin/linux/${JL_DIR}/${JL_MINOR}/julia-${JULIA_VERSION}-linux-${JL_ARCH}.tar.gz" \
        | tar -xz -C /opt; \
    ln -s "/opt/julia-${JULIA_VERSION}/bin/julia" /usr/local/bin/julia; \
    julia --version

# YasolSolver.jl pins JuMP 1.0.0 and MathOptInterface <= 1.1.1, so the resolved
# environment is deliberately old. Precompile at build time to keep requests fast.
COPY julia/setup.jl /tmp/setup.jl
# Packages added from a git URL are checked out mode 0700, unlike registry packages
# unpacked at 0755. Without the chmod, the non-root runtime user cannot read
# YasolSolver and Julia reports it as "required but does not seem to be installed".
RUN julia /tmp/setup.jl \
    && chmod -R a+rX /opt/julia-depot /opt/julia-env \
    && rm /tmp/setup.jl

# --- API ------------------------------------------------------------------
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY Yasol.ini /opt/yasol/Yasol.ini
COPY api/ /opt/yasol-api/

# Jobs run as a non-root user in a scratch directory.
RUN useradd --create-home --uid 1000 yasol \
    && mkdir -p /work && chown yasol:yasol /work \
    && install -d -o yasol -g yasol /home/yasol/.julia

# Julia insists on a writable depot (usage logs, precompile locks, scratchspaces), but
# the packages installed above sit in a root-owned depot. Giving the runtime user its
# own depot *first* keeps the shared one read-only while staying fully usable: package
# sources and the precompiled caches built above are still found on the second entry.
ENV JULIA_DEPOT_PATH=/home/yasol/.julia:/opt/julia-depot

USER yasol
WORKDIR /work

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "--app-dir", "/opt/yasol-api", "main:app", "--host", "0.0.0.0", "--port", "8000"]
