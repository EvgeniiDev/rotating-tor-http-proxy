@echo off
echo 🚀 Starting Tor Proxy with Admin Panel...

REM Build and start the container
docker-compose up --build -d

REM Wait a moment for services to start
timeout /t 5 /nobreak > nul

echo ✅ Services started successfully!
echo.
echo 📊 Admin Panel: http://localhost:5000
echo 🌐 HTTP Proxy: http://localhost:8080
echo 📈 HAProxy Stats: http://localhost:4444/stats
echo.
echo 🔍 To view logs: docker-compose logs -f
echo 🛑 To stop: docker-compose down

