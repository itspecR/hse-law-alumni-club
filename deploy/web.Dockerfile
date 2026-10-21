FROM python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS deps
RUN pip install --no-cache-dir uv==0.13.0
WORKDIR /repo
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY frontend/pyproject.toml frontend/uv.lock frontend/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --project frontend --frozen --no-dev --no-install-project

FROM deps AS build
COPY frontend/club_web frontend/club_web
COPY frontend/public frontend/public
COPY data data
RUN --mount=type=cache,target=/root/.cache/uv uv sync --project frontend --frozen --no-dev --no-editable && \
    /opt/venv/bin/python -m club_web.build --output /opt/venv/lib/python3.14/site-packages/club_web/public && \
    rm /opt/venv/lib/python3.14/site-packages/club_web/mirror.py /opt/venv/lib/python3.14/site-packages/club_web/sync_changes.py /opt/venv/lib/python3.14/site-packages/club_web/build.py && \
    /opt/venv/bin/python -m compileall -q -b -s /opt/venv/lib/python3.14/site-packages -p /app /opt/venv/lib/python3.14/site-packages/club_web && \
    find /opt/venv/lib/python3.14/site-packages/club_web -name '*.py' -delete && \
    find /opt/venv/lib/python3.14/site-packages/club_web -type d -name __pycache__ -exec rm -rf {} + && \
    rm -rf /opt/venv/lib/python3.14/site-packages/club_web/browser /opt/venv/lib/python3.14/site-packages/club_web/styles && \
    find /opt/venv -name direct_url.json -delete

FROM python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72
ARG VCS_REF
LABEL org.opencontainers.image.revision=$VCS_REF
RUN apk upgrade --no-cache && apk add --no-cache ca-certificates && \
    addgroup -g 1000 club && adduser -D -u 1000 -G club club && \
    rm -rf /usr/local/lib/python3.14/site-packages/pip* /usr/local/bin/pip*
COPY --from=deps /opt/venv /opt/venv
COPY --from=build /opt/venv/lib/python3.14/site-packages/club_web /opt/venv/lib/python3.14/site-packages/club_web
COPY --from=build /opt/venv/lib/python3.14/site-packages/club_web-0.1.0.dist-info /opt/venv/lib/python3.14/site-packages/club_web-0.1.0.dist-info
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WEB_HOST=0.0.0.0 WEB_PORT=80 API_INTERNAL_URL=http://api:3000
WORKDIR /app
USER club
EXPOSE 80
CMD ["python", "-m", "club_web.main"]
