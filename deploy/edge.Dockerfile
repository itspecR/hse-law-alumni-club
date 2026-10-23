FROM golang:1.27.3-alpine@sha256:b8c9b82affbbb82a020bbcf998dad2ba9516944ac7e883524642a481a9505e9c AS edge-source
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMAXPROCS=2
WORKDIR /caddy
COPY deploy/caddy/go.mod deploy/caddy/go.sum ./locked/
COPY deploy/caddy/go.mod deploy/caddy/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download && go mod verify
COPY deploy/caddy/main.go ./
COPY deploy/caddy/tests ./tests

FROM edge-source AS edge-lock-update
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go mod tidy

FROM scratch AS edge-lock-export
COPY --from=edge-lock-update /caddy/go.mod /caddy/go.sum /

FROM edge-source AS edge-build
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    cmp go.mod locked/go.mod && cmp go.sum locked/go.sum && \
    go mod vendor && \
    test ! -d vendor/github.com/google/cel-go && \
    go list -mod=vendor -deps . > /caddy/dependencies.txt && \
    ! grep -E '^golang.org/x/crypto/openpgp(/|$)' /caddy/dependencies.txt && \
    go build -mod=vendor -p 2 -trimpath -ldflags='-s -w' -o /usr/bin/caddy . && \
    go version -m /usr/bin/caddy > /caddy/build-info.txt && \
    grep -E 'dep[[:space:]]+github.com/caddyserver/caddy/v2[[:space:]]+v2.11.7[[:space:]]' /caddy/build-info.txt && \
    grep -E 'dep[[:space:]]+cel.dev/cel-go[[:space:]]+v0.32.0[[:space:]]' /caddy/build-info.txt && \
    grep -E 'dep[[:space:]]+github.com/KimMachineGun/automemlimit[[:space:]]+v1.0.0[[:space:]]' /caddy/build-info.txt && \
    ! grep -F 'github.com/google/cel-go' /caddy/build-info.txt

FROM edge-build AS edge-test
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go test -mod=vendor -p 2 -v ./...
FROM edge-test AS edge-check
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go install -p 2 golang.org/x/vuln/cmd/govulncheck@v1.9.0 && \
    GOMEMLIMIT=2GiB GOFLAGS=-mod=vendor \
    /go/bin/govulncheck -show verbose,traces ./...

FROM alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS caddy
ARG VCS_REF
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.version="v2.11.7" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV CADDY_VERSION=v2.11.7 XDG_CONFIG_HOME=/config XDG_DATA_HOME=/data
RUN apk add --no-cache ca-certificates curl mailcap 'zlib>=1.3.2-r1' && \
    mkdir -p /config/caddy /data/caddy /etc/caddy /usr/share/caddy && \
    chmod 1777 /config/caddy /data/caddy
COPY --from=edge-build /usr/bin/caddy /usr/bin/caddy
COPY --from=edge-build /caddy/dependencies.txt /usr/share/caddy/dependencies.txt
COPY deploy/caddy/LICENSE /usr/share/licenses/club-caddy/LICENSE
COPY deploy/Caddyfile /etc/caddy/Caddyfile
RUN caddy version
EXPOSE 80 443 443/udp 2019
WORKDIR /srv
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
