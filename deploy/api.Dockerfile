FROM python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS deps
RUN apk add --no-cache build-base mariadb-connector-c-dev pkgconf
RUN pip install --no-cache-dir uv==0.13.0
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /repo/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python

FROM deps AS build
COPY data/domain-data.json data/faq-data.json data/legal-data.json /repo/data/
COPY backend/club_api ./club_api
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --reinstall-package club-api --python /usr/local/bin/python
RUN .venv/bin/python -m compileall -q -b -s /repo -p /app .venv/lib/python3.14/site-packages/club_api \
    && find .venv/lib/python3.14/site-packages/club_api -type f -name '*.py' -delete \
    && find .venv/lib/python3.14/site-packages/club_api -type d -name __pycache__ -exec rm -rf {} + \
    && rm .venv/lib/python3.14/site-packages/club_api-0.1.0.dist-info/direct_url.json

FROM python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS runtime
ARG VCS_REF
LABEL org.opencontainers.image.revision=$VCS_REF
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN apk upgrade --no-cache && apk add --no-cache mariadb-connector-c
RUN addgroup -g 1000 club && adduser -D -H -u 1000 -G club club \
    && mkdir -p /data/uploads && chown club:club /data/uploads \
    && rm -rf /usr/local/lib/python3.14/site-packages/pip* /usr/local/bin/pip*
WORKDIR /app
COPY --from=deps /repo/backend/.venv /opt/venv
COPY --from=build /repo/backend/.venv/lib/python3.14/site-packages/club_api /opt/venv/lib/python3.14/site-packages/club_api
COPY --from=build /repo/backend/.venv/lib/python3.14/site-packages/club_api-0.1.0.dist-info /opt/venv/lib/python3.14/site-packages/club_api-0.1.0.dist-info
USER club
EXPOSE 3000
CMD ["python", "-m", "club_api.main"]
