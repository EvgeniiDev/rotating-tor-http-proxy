#!/bin/bash

IMAGE_NAME="rotating-tor-proxy"
CONTAINER_NAME="tor-proxy"

MEMORY_LIMIT=${MEMORY_LIMIT:-"1g"}
TOR_PROCESSES=${TOR_PROCESSES:-"50"}

echo "Building Docker image..."
docker build -t $IMAGE_NAME .

echo "Stopping existing container..."
docker stop $CONTAINER_NAME 2>/dev/null || true
docker rm $CONTAINER_NAME 2>/dev/null || true

echo "Starting container with memory limits:"
echo "  Memory Limit: $MEMORY_LIMIT"
echo "  Max Memory: $MAX_MEMORY"
echo "  Tor Processes: $TOR_PROCESSES"

docker run -d \
    --name $CONTAINER_NAME \
    -p 5000:5000 \
    -p 8080:8080 \
    --memory=$MEMORY_LIMIT \
    -e TOR_PROCESSES=$TOR_PROCESSES \
    $IMAGE_NAME

echo "Container started successfully!"
echo "Web interface: http://localhost:5000"
echo "HTTP proxy: http://localhost:8080"
echo "Health check: http://localhost:5000/health"

echo "Logs:"
docker logs -f $CONTAINER_NAME
