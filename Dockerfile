FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 cineswarm \
    && useradd --uid 10001 --gid cineswarm --home-dir /app --no-create-home cineswarm \
    && mkdir -p /data /backups \
    && chown cineswarm:cineswarm /data /backups
COPY --chown=cineswarm:cineswarm *.py ./

USER cineswarm
EXPOSE 8787
VOLUME ["/data", "/backups"]
CMD ["python", "cineswarm_control.py", "--host", "0.0.0.0", "--port", "8787"]
