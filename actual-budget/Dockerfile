ARG BUILD_FROM=ghcr.io/hassio-addons/base:21.0.5
ARG ACTUAL_VERSION="26.8.1"

# Actual publishes its server to npm with the web client as a dependency, so
# the whole application arrives as one install and none of the monorepo build
# is needed here.
#
# Underneath it sit three native modules, though: the database and the two
# password hashes. They publish musl builds today and are installed rather than
# compiled, but which prebuilt binaries exist is upstream's business and moves
# with every Node release, so the toolchain to build them from source is here
# for when one is missing. That toolchain is why this is a stage of its own.
# hadolint ignore=DL3006
FROM ${BUILD_FROM} AS builder
ARG ACTUAL_VERSION

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV NODE_ENV=production

WORKDIR /opt/actual

# The client is built for the site root, which Ingress does not have. This
# moves the addresses it resolves against onto the document base, where NGINX
# can settle them per request.
COPY patches /patches

# Nothing leaves this stage but /opt/actual, so the toolchain is installed and
# left where it lies; only what ends up inside that directory is worth removing,
# which is the object files a compiled module leaves next to its binary.
# hadolint ignore=DL3016
RUN \
    apk add --no-cache \
        build-base=0.5-r4 \
        nodejs=24.18.1-r0 \
        npm=11.12.1-r0 \
        python3=3.14.7-r1 \
    \
    && npm install \
        --no-audit \
        --no-fund \
        --no-update-notifier \
        --omit=dev \
        "@actual-app/sync-server@${ACTUAL_VERSION}" \
    \
    && python3 /patches/runtime-base-path.py \
        /opt/actual/node_modules/@actual-app/web/build \
    \
    && find /opt/actual/node_modules -type d -name obj.target -exec rm -f -r {} + \
    && find /opt/actual/node_modules -type f -name '*.o' -delete

# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# NGINX faces Ingress and the network, and is what writes the base path into
# the page on the way out. Actual itself only ever listens on loopback.
RUN \
    apk add --no-cache \
        nginx=1.30.4-r1 \
        nodejs=24.18.1-r0 \
    \
    && rm -f -r \
        /etc/nginx \
        /tmp/*

COPY --from=builder /opt/actual /opt/actual

# Copy root filesystem
COPY rootfs /

# Health check
HEALTHCHECK \
    CMD curl --silent --fail http://127.0.0.1:49000/health || exit 1

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Franck Nijhof <opensource@frenck.dev>" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Home Assistant Community Apps" \
    org.opencontainers.image.authors="Franck Nijhof <opensource@frenck.dev>" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://frenck.dev/home-assistant-apps" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
