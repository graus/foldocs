# Build stage: install the dependencies with uv, which the running image does not need.
FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39 AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.8@sha256:88234bc9e09c2b2f6d176a3daf411419eb0370d450a08129257410de9cfafd2a /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY foldocs ./foldocs
# The bundled themes: to use as is, or to extend from a theme mounted at THEME_DIR.
COPY themes ./themes
RUN uv sync --frozen --no-dev --no-editable

# Run stage: Python, the virtualenv, the code and the themes; nothing else.
FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY --from=build /app /app
# A uid no host account has, so files mounted for the container are readable by it alone. The
# container starts as root only to make sure the data volume is that user's (a volume can come
# up owned by root), then runs as it.
RUN useradd --system --uid 10001 foldocs && mkdir /data && chown foldocs /data
ENTRYPOINT ["sh", "-c", "[ \"$(stat -c %u /data)\" = 10001 ] || chown -R foldocs: /data; exec setpriv --reuid=foldocs --regid=foldocs --init-groups \"$@\"", "--"]
CMD ["/app/.venv/bin/foldocs", "loop"]
