FROM nginx:1.30.6-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951
ARG VCS_REF
LABEL org.opencontainers.image.revision=$VCS_REF
RUN apk upgrade --no-cache && apk add --no-cache 'libexpat>=2.8.5-r0' 'pcre2>=10.49-r0'
USER 101:101
