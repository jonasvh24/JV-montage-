# check=skip=SecretsUsedInArgOrEnv
# nullmontage as a password-protected web service.
#   docker build -t nullmontage .
#   docker run -p 8765:8765 -e NULLMONTAGE_PASSWORD=... -v nullmontage-data:/app/data nullmontage
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

# DejaVu Bold is the Linux entry in effects._FONT_CANDIDATES; without it intertitles fall back to
# Pillow's bitmap font. ffmpeg needs no package: imageio-ffmpeg ships its own binary.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Dependencies in their own layer so a code change doesn't reinstall librosa, numba and co.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY nullmontage ./nullmontage
COPY static ./static
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app:app /app/data
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# NULLMONTAGE_REQUIRE_PASSWORD makes the server refuse to start without NULLMONTAGE_PASSWORD.
# librosa's numba JIT caches next to its own source by default, which the app user cannot write.
ENV PATH=/app/.venv/bin:$PATH \
    NULLMONTAGE_HOST=0.0.0.0 \
    PORT=8765 \
    NULLMONTAGE_REQUIRE_PASSWORD=1 \
    NUMBA_CACHE_DIR=/tmp/numba \
    PYTHONUNBUFFERED=1

VOLUME /app/data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8765'))" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["nullmontage"]
