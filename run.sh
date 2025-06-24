#!/bin/bash
SERVICE_NAME="tor-http-proxy"
export TOR_PROCESSES=200
systemctl start $SERVICE_NAME