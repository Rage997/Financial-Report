# FinancialReport app image.
#
# Public sources only: private/, uploads, generated outputs, .git and
# developer files are excluded from the build context by .dockerignore.
# All writable paths live under /app/private, which Docker Compose bind-mounts
# from the host's ./private folder.

FROM python:3.13-slim

# Non-root runtime user. Linux bind mounts can use FINREPORT_UID/FINREPORT_GID
# in Compose to match the host owner without changing financial-file ownership.
RUN groupadd --gid 1000 finreport \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin finreport

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib

# Pinned dependencies first for layer caching.
COPY --chown=finreport:finreport requirements.txt .
RUN pip install --no-cache-dir --requirement requirements.txt

# Explicit public source paths only.
COPY --chown=finreport:finreport app.py ./
COPY --chown=finreport:finreport finreport/ ./finreport/
COPY --chown=finreport:finreport static/ ./static/
COPY --chown=finreport:finreport examples/ ./examples/
COPY --chown=finreport:finreport config.json rules.json ./

USER finreport

# Cache bundled font discovery in the image, not during the first chart export.
RUN python -c "from matplotlib import font_manager" \
    && chmod -R a+rX /tmp/matplotlib \
    && chmod a+rwx /tmp/matplotlib

# Loopback exposure is enforced by the Compose port mapping
# (127.0.0.1:${FINREPORT_PORT:-8010}:8010); inside the container uvicorn must
# bind all interfaces to be reachable from the Compose network.
EXPOSE 8010
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
