FROM python:3.13-slim

# Build context is the repo root (see docker-compose.yml).
COPY backend/ /opt/ytui-server/

RUN pip install --no-cache-dir /opt/ytui-server[auth] \
    # yt-dlp nightly (PyPI pre-releases): YouTube is rolling out PO-token
    # enforcement on android_vr (Aug 2026), the only anonymous client the
    # stable release serves — every VOD/live then 403s mid-stream. The fix
    # (visionos client, PR #17261) is only in nightly; measured healthy
    # end-to-end from this server's IP on 2026-08-18.
    && pip install --no-cache-dir --upgrade --pre "yt-dlp[default]"

RUN useradd --system --create-home --uid 900 app \
    && mkdir -p /data && chown app:app /data

USER app
EXPOSE 8776
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8776/health', timeout=4)"

CMD ["uvicorn", "ytui_server.main:app", "--host", "0.0.0.0", "--port", "8776"]
