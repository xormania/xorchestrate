FROM php:8.5.11-cli-bookworm@sha256:d551e79d694fd91c4fdf34c4adcc52dd2042a064b881e25c319b2533ead0682c

COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /usr/local/bin/uv
COPY debian.sources requirements-build.txt /opt/serena-build/

# Serena remains an external GPL-3.0-or-later service. Its exact source and license
# are retained in the image; no Serena implementation is copied into devtools.
ARG SERENA_REVISION=b79e2a55f9d4072084977dd35bd07d31146d88b9
LABEL org.opencontainers.image.source="https://github.com/oraios/serena" \
      org.opencontainers.image.revision="$SERENA_REVISION"
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
    && rm -rf /var/lib/apt/lists/* \
    && cp /opt/serena-build/debian.sources /etc/apt/sources.list.d/debian.sources \
    && apt-get update -o APT::Update::Error-Mode=any \
    && apt-get install -y --no-install-recommends \
    python3 git curl ca-certificates libonig-dev \
    && docker-php-ext-install mbstring \
    && rm -rf /var/lib/apt/lists/*
RUN git init /opt/serena-source \
    && git -C /opt/serena-source remote add origin https://github.com/oraios/serena.git \
    && git -C /opt/serena-source fetch --depth=1 origin "$SERENA_REVISION" \
    && git -C /opt/serena-source checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/serena-source rev-parse HEAD)" = "$SERENA_REVISION" \
    && uv --no-cache venv --python /usr/bin/python3 /opt/serena-env \
    && uv --no-cache pip install --python /opt/serena-env/bin/python \
        --require-hashes --only-binary :all: -r /opt/serena-build/requirements-build.txt \
    && UV_PROJECT_ENVIRONMENT=/opt/serena-env UV_PYTHON_DOWNLOADS=never \
        uv --no-cache sync --project /opt/serena-source --python /usr/bin/python3 \
        --locked --no-dev --no-editable --no-build-isolation \
    && uv pip check --python /opt/serena-env/bin/python \
    && git -C /opt/serena-source diff --exit-code -- pyproject.toml uv.lock
# Match the dependency pinned by this Serena revision, available without runtime network.
RUN curl --fail --location --silent --show-error \
    https://github.com/phpactor/phpactor/releases/download/2025.12.21.1/phpactor.phar \
    --output /opt/phpactor.phar \
    && echo '53bbe9625cd9b5e9b394bc2f595fbad13dbbe6dfc96950c56dea3b5d9a246cc3  /opt/phpactor.phar' | sha256sum --check
COPY entry.py /opt/serena-entry.py
COPY process_audit.py /opt/process_audit.py
ENV PATH="/opt/serena-env/bin:$PATH" HOME=/tmp/home SERENA_HOME=/tmp/serena PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
ENTRYPOINT ["/opt/serena-env/bin/python", "/opt/serena-entry.py"]
