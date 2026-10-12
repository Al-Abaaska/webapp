FROM docker.io/runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

ENV PYTHONUNBUFFERED=1
WORKDIR /app

ARG DENO_VERSION=v2.9.5
ARG BGUTIL_VERSION=1.3.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg unzip \
    && curl -fsSL -o /tmp/deno.zip \
       "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && deno --version \
    && rm -f /tmp/deno.zip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/bgutil.zip \
       "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/${BGUTIL_VERSION}.zip" \
    && unzip /tmp/bgutil.zip -d /opt \
    && mv "/opt/bgutil-ytdlp-pot-provider-${BGUTIL_VERSION}" /opt/bgutil-ytdlp-pot-provider \
    && cd /opt/bgutil-ytdlp-pot-provider/server \
    && deno install --allow-scripts=npm:canvas --frozen \
    && rm -f /tmp/bgutil.zip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY handler.py .
CMD ["python", "-u", "handler.py"]
