FROM python:3.12-alpine

WORKDIR /app

# Chromium itself now runs in a separate sidecar container (see
# docker-compose.yml) reached over the DevTools protocol via BROWSER_CDP_URL,
# so this image only needs the Playwright *client* - not a bundled browser.
# That's what makes an Alpine base workable at all: Playwright's browsers
# (and its own Node driver binary) aren't musl-compatible, but we only need
# the driver's pure-JS/Python parts here, run against Alpine's own Node.
RUN apk add --no-cache nodejs

COPY requirements.txt .

# Everything except playwright installs normally (all these deps ship
# musllinux wheels). Playwright itself has no musllinux wheel, but it's pure
# Python + bundled JS - only the embedded `node` binary is glibc-specific,
# and we never use it (PLAYWRIGHT_NODEJS_PATH below points at apk's node
# instead). So we deliberately pull the manylinux wheel for the current
# target arch and skip its (already-satisfied) dependency resolution.
ARG TARGETARCH
RUN grep -v '^playwright==' requirements.txt > requirements.notplaywright.txt \
    && pip install --no-cache-dir -r requirements.notplaywright.txt \
    && case "$TARGETARCH" in \
         amd64) PLATFORM_TAG=manylinux1_x86_64 ;; \
         arm64) PLATFORM_TAG=manylinux_2_17_aarch64 ;; \
         *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && SITE_PACKAGES="$(python -c 'import site; print(site.getsitepackages()[0])')" \
    && pip install --no-cache-dir --no-deps --only-binary=:all: --target "$SITE_PACKAGES" \
         --platform "$PLATFORM_TAG" --python-version 312 --implementation cp --abi cp312 \
         $(grep '^playwright==' requirements.txt)

ENV PLAYWRIGHT_NODEJS_PATH=/usr/bin/node

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/docs')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
