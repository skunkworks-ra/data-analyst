# Tight CI image: just the tools needed to install and exercise the plugin.
# Repo code is checked out and mounted at run time, not baked into the image,
# so this only needs to be rebuilt when the toolchain itself changes.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git bash xz-utils \
    && rm -rf /var/lib/apt/lists/*

# pixi — manages the Python/casatools environment declared in pixi.toml
RUN curl -fsSL https://pixi.sh/install.sh | bash \
    && mv /root/.pixi/bin/pixi /usr/local/bin/pixi

# Node — required by the Claude Code CLI installer
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /workspace
