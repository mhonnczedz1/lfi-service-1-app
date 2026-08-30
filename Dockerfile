# -slim, not -alpine. Alpine uses musl instead of glibc, which means many
# Python wheels have no prebuilt binary and get compiled from source instead.
# Slim is slightly larger and vastly less annoying.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE  no .pyc files; the container is disposable
# PYTHONUNBUFFERED         flush stdout immediately, so `kubectl logs` is live
#                          rather than showing nothing until the buffer fills
# PIP_NO_CACHE_DIR         do not keep a pip cache in the image layer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so this layer caches across source-only changes.
# Reversing these two COPYs would reinstall every dependency on every source
# edit, which is the single most common slow-Dockerfile mistake.
COPY requirements.txt .
# --index-url is belt and braces. A container never sees your host's
# ~/.config/pip/pip.conf, so this already resolved from public PyPI, but
# saying so means a stray PIP_INDEX_URL in the build environment cannot
# quietly redirect it. See the primer at the top of Section 3.
RUN pip install --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt

# requirements.txt, not requirements-dev.txt: pytest and ruff have no business
# in a production image. .dockerignore also excludes tests/.
COPY src/ ./src/

# Run as a non-root user. A container that does not need root should
# not have it, and some clusters refuse to schedule ones that do.
#
# A high, explicit uid rather than a name, because Kubernetes securityContext
# checks work on numeric ids. Task 3.3.2 asserts this is 10001, not 0.
# chown after COPY, because files copied earlier are owned by root.
RUN useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

# Documentation only. It publishes nothing; the Deployment's containerPort
# and the Service are what actually route traffic.
EXPOSE 8000

# Exec form (a JSON array), not shell form. Shell form wraps the process in
# /bin/sh, which swallows SIGTERM, so Kubernetes would wait out the full
# termination grace period on every pod delete instead of stopping cleanly.
#
# --host 0.0.0.0, not 127.0.0.1: bind on all interfaces or nothing outside
# the container can reach it.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
