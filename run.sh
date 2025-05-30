#!/bin/bash

echo "🚀 Starting Tor Proxy with Admin Panel..."

# Build and start the container
docker-compose up --build -d

# Wait a moment for services to start
sleep 5

echo "✅ Services started successfully!"
echo ""
echo "📊 Admin Panel: http://localhost:5000"
echo "🌐 HTTP Proxy: http://localhost:3128"
echo "📈 HAProxy Stats: http://localhost:4444/stats"
echo ""
echo "🔍 To view logs: docker-compose logs -f"
echo "🛑 To stop: docker-compose down"
