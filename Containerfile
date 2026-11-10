FROM docker.io/library/node:22-bookworm-slim

ARG CODEX_VERSION

LABEL org.opencontainers.image.title="codex-podman-isolated" \
      org.opencontainers.image.description="OpenAI Codex CLI in a minimal Podman container" \
      org.opencontainers.image.version="${CODEX_VERSION}"

RUN test -n "$CODEX_VERSION" \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gh \
        git \
        jq \
        less \
        openssh-client \
        procps \
        python3 \
        ripgrep \
    && npm install -g "@openai/codex@${CODEX_VERSION}" \
    && rm -rf /var/lib/apt/lists/* /root/.npm

COPY entrypoint.sh /usr/local/bin/codex-container-entrypoint
RUN chmod 0755 /usr/local/bin/codex-container-entrypoint \
    && mkdir -p /state/.codex /workspace

ENV HOME=/state
ENV CODEX_HOME=/state/.codex

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/codex-container-entrypoint"]
