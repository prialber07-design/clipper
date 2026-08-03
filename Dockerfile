# Imagen CPU. Para GPU, usa Dockerfile.gpu (ver DESPLIEGUE.md).
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig ca-certificates tini procps tzdata curl bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Las dependencias van antes que el codigo: cambiar un .py no reinstala nada.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py *.sh config.json ./
COPY fonts ./fonts
RUN mkdir -p /usr/local/share/fonts/clipper \
    && cp fonts/*.ttf /usr/local/share/fonts/clipper/ \
    && fc-cache -f
RUN chmod +x *.sh

# El codigo va como root y de solo lectura; los datos, de un usuario sin
# privilegios sobre el volumen.
RUN useradd --system --uid 10001 clipper \
    && mkdir -p /app/clips/modelos \
    && chown -R clipper:clipper /app/clips

ENV CLIPPER_DATA=/app/clips \
    HF_HOME=/app/clips/modelos \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Europe/Madrid \
    OMP_NUM_THREADS=8 \
    MKL_NUM_THREADS=8 \
    OPENBLAS_NUM_THREADS=8 \
    CLIPPER_CPU_THREADS=8

USER clipper
VOLUME ["/app/clips"]
EXPOSE 8080

# tini recoge los zombis de streamlink y ffmpeg, que se lanzan por cada canal.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Sin vigilantes vivos el contenedor no sirve de nada: que el orquestador lo sepa.
HEALTHCHECK --interval=2m --timeout=20s --start-period=90s --retries=3 \
    CMD python servidor.py --estado 2>&1 | grep -q "vigilante=activo" || exit 1

CMD ["python", "servidor.py"]
