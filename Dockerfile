# syntax=docker/dockerfile:1.7
FROM python:3.12.7-slim-bookworm@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels --require-hashes -r requirements.lock \
    && python -m pip wheel --wheel-dir /wheels --no-deps .

FROM python:3.12.7-slim-bookworm@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d AS runtime

ARG VERSION=dev
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="CaseOps" \
      org.opencontainers.image.description="Production-grade multi-agent reference system" \
      org.opencontainers.image.source="https://github.com/dataPro-lgtm/production-grade-multi-agent-caseops" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/caseops/bin:${PATH}"

RUN groupadd --system --gid 10001 caseops \
    && useradd --system --uid 10001 --gid caseops --home /opt/caseops caseops

WORKDIR /opt/caseops
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/container-entrypoint.sh ./scripts/container-entrypoint.sh
RUN chmod 0555 ./scripts/container-entrypoint.sh \
    && chown -R caseops:caseops /opt/caseops

USER 10001:10001
EXPOSE 8080 8081 8082

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=2)"]

ENTRYPOINT ["./scripts/container-entrypoint.sh"]
