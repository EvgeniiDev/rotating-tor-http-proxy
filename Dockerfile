FROM python:3.13.3-alpine3.22

EXPOSE 5000/tcp 8080/tcp

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TOR_PROCESSES=50

RUN apk --no-cache --no-progress --quiet add \
    tor \
    git \
    wget \
    && rm -rf /var/cache/apk/*

COPY src/requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY start_new.py ./
COPY src/ ./src/

RUN addgroup proxy && \
    adduser -S -D -u 1000 -G proxy proxy && \
    mkdir -p /home/proxy/.tor_proxy/config && \
    mkdir -p /home/proxy/.tor_proxy/data && \
    mkdir -p /home/proxy/.tor_proxy/logs && \
    chown -R proxy: /home/proxy

USER proxy

WORKDIR /home/proxy

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:5000/health || exit 1

CMD ["python3", "/start_new.py"]