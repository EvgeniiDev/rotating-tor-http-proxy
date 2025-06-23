#!/bin/bash

CONTAINER_NAME="tor-proxy"

echo "Stopping container..."
docker stop $CONTAINER_NAME 2>/dev/null || true
docker rm $CONTAINER_NAME 2>/dev/null || true

echo "Container stopped and removed."
