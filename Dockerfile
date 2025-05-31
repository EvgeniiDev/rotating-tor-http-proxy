FROM python:3.13.3-alpine3.22

ENV \
    # sets the number of tor instances
    TOR_INSTANCES=10 \
    # sets the interval (in seconds) to rebuild tor circuits
    TOR_REBUILD_INTERVAL=1800

EXPOSE 3128/tcp 4444/tcp 5000/tcp

COPY tor.cfg privoxy.cfg haproxy.cfg start_with_admin.sh admin_panel.py config_manager.py requirements.txt ./
COPY templates/ templates/

RUN apk --no-cache --no-progress --quiet add tor bash privoxy haproxy curl sed && \
    # Install Python dependencies (no need for --break-system-packages with official Python image)
    pip3 install --no-cache-dir -r requirements.txt && \
    # move configuration files
    mv /tor.cfg /etc/tor/torrc.default && \
    mv /privoxy.cfg /etc/privoxy/config.templ && \
    mv /haproxy.cfg /etc/haproxy/haproxy.cfg.default && \
    chmod +x /start_with_admin.sh && \
    chmod +x /admin_panel.py && \
    #
    # prepare for low-privilege execution
    addgroup proxy && \
    adduser -S -D -u 1000 -G proxy proxy && \
    touch /etc/haproxy/haproxy.cfg && \
    chown -R proxy: /etc/haproxy/ && \
    mkdir -p /var/lib/haproxy && \
    chown -R proxy: /var/lib/haproxy && \
    mkdir -p /var/local/haproxy && \
    chown -R proxy: /var/local/haproxy && \
    touch /etc/tor/torrc && \
    chown -R proxy: /etc/tor/ && \
    chown -R proxy: /etc/privoxy/ && \
    mkdir -p /var/local/tor && \
    chown -R proxy: /var/local/tor && \
    mkdir -p /var/lib/tor && \
    chown -R proxy: /var/lib/tor && \
    mkdir -p /var/log/tor && \
    chown -R proxy: /var/log/tor && \
    mkdir -p /var/run/tor && \
    chown -R proxy: /var/run/tor && \
    mkdir -p /var/local/privoxy && \
    chown -R proxy: /var/local/privoxy && \
    mkdir -p /var/log/privoxy && \
    chown -R proxy: /var/log/privoxy && \
    #
    # cleanup
    #
    # tor
    rm -rf /etc/tor/torrc.sample && \
    # privoxy
    rm -rf /etc/privoxy/*.new /etc/logrotate.d/privoxy && \
    # files like /etc/shadow-, /etc/passwd-
    find / -xdev -type f -regex '.*-$' -exec rm -f {} \; && \
    # temp and cache
    rm -rf /var/cache/apk/* /usr/share/doc /usr/share/man/ /usr/share/info/* /var/cache/man/* /tmp/* /etc/fstab && \
    # init scripts
    rm -rf /etc/init.d /lib/rc /etc/conf.d /etc/inittab /etc/runlevels /etc/rc.conf && \
    # kernel tunables
    rm -rf /etc/sysctl* /etc/modprobe.d /etc/modules /etc/mdev.conf /etc/acpi

STOPSIGNAL SIGINT

USER proxy

CMD ["/start_with_admin.sh"]
