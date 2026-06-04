#!/bin/bash
set -e

echo "============================================"
echo "  IoA Distributed Network Ops Platform"
echo "============================================"
echo ""

# Check .env
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    echo "[!] .env not found. Copying from .env.example..."
    cp .env.example .env
    echo "[!] Please edit .env with your actual keys, then re-run."
    exit 1
  else
    echo "[ERROR] Neither .env nor .env.example found."
    exit 1
  fi
fi

# Load env vars
set -a
source .env
set +a

echo "[*] Building Docker images..."
docker compose build

echo ""
echo "[*] Starting services..."
docker compose up -d

echo ""
echo "============================================"
echo "  Services started!"
echo "============================================"
echo ""
echo "  GUI:        http://localhost:3000"
echo "  Middleware: http://localhost:8000"
echo "  Simulator:  http://localhost:8001"
echo "  NATS Admin: http://localhost:8222"
echo "  API Docs:   http://localhost:8000/docs"
echo ""
echo "  View logs:  docker compose logs -f"
echo "  Stop:       docker compose down"
echo ""
