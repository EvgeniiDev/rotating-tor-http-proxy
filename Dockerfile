FROM python:3.13.3-alpine3.22

EXPOSE 8080/tcp 4444/tcp 5000/tcp

# Install system packages in separate layer for better caching
# Fix HAProxy version to 3.0 for consistent command compatibility and modern features
# Add Privoxy for SOCKS to HTTP conversion
RUN apk --no-cache --no-progress --quiet add tor socat haproxy~=3.0 privoxy

COPY src/requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY src/ ./

RUN cp /haproxy.cfg /etc/haproxy/haproxy.cfg && \
    chmod +x /start_with_admin.sh && \
    chmod +x /admin_panel.py && \
    #
    # prepare for low-privilege execution
    addgroup proxy && \
    adduser -S -D -u 1000 -G proxy proxy && \
    chown -R proxy: /etc/haproxy/ && \
    mkdir -p /var/lib/haproxy && \
    chown -R proxy: /var/lib/haproxy && \
    mkdir -p /var/local/haproxy && \
    chown -R proxy: /var/local/haproxy && \
    # Create the server state file for HAProxy
    touch /var/local/haproxy/server-state && \
    chown proxy: /var/local/haproxy/server-state && \
    chown -R proxy: /etc/tor/ && \
    mkdir -p /var/local/tor && \
    chown -R proxy: /var/local/tor && \
    mkdir -p /var/lib/tor && \
    chown -R proxy: /var/lib/tor && \
    mkdir -p /var/log/tor && \
    chown -R proxy: /var/log/tor && \
    mkdir -p /var/run/tor && \
    chown -R proxy: /var/run/tor && \
    # Create directories for Privoxy configurations
    mkdir -p /tmp/privoxy_configs && \
    chown -R proxy: /tmp/privoxy_configs && \
    mkdir -p /var/log/privoxy && \
    chown -R proxy: /var/log/privoxy && \
    # Create a writable tmp directory for the proxy user
    mkdir -p /home/proxy/tmp && \
    chown -R proxy: /home/proxy/tmp

STOPSIGNAL SIGINT

USER proxy

CMD ["sh", "start_with_admin.sh"]
