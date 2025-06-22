FROM python:3.13.3-alpine3.22

EXPOSE 5000/tcp 8080/tcp

RUN apk --no-cache --no-progress --quiet add tor

COPY src/requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY start_new.py ./
COPY src/ ./src/

RUN chmod +x /start_new.py && \
    chmod +x /src/start_with_admin.sh && \
    #
    # prepare for low-privilege execution
    addgroup proxy && \
    adduser -S -D -u 1000 -G proxy proxy && \
    # Create tor proxy directories in user home
    mkdir -p /home/proxy/.tor_proxy/config && \
    mkdir -p /home/proxy/.tor_proxy/data && \
    mkdir -p /home/proxy/.tor_proxy/logs && \
    mkdir -p /home/proxy/tmp && \
    chown -R proxy: /home/proxy

STOPSIGNAL SIGINT

USER proxy

CMD ["python3", "start_new.py"]
