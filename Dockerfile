FROM python:3.13.3-alpine3.22

EXPOSE 8080/tcp 4444/tcp 5000/tcp

RUN apk --no-cache --no-progress --quiet add tor

COPY src/requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY src/ ./

RUN chmod +x /start_with_admin.sh && \
    chmod +x /admin_panel.py && \
    #
    # prepare for low-privilege execution
    addgroup proxy && \
    adduser -S -D -u 1000 -G proxy proxy && \
    chown -R proxy: /etc/tor/ && \
    mkdir -p /var/local/tor && \
    chown -R proxy: /var/local/tor && \
    mkdir -p /var/lib/tor && \
    chown -R proxy: /var/lib/tor && \
    mkdir -p /var/log/tor && \
    chown -R proxy: /var/log/tor && \
    mkdir -p /var/run/tor && \
    chown -R proxy: /var/run/tor && \
    # Create a writable tmp directory for the proxy user
    mkdir -p /home/proxy/tmp && \
    chown -R proxy: /home/proxy/tmp

STOPSIGNAL SIGINT

USER proxy

CMD ["sh", "start_with_admin.sh"]
