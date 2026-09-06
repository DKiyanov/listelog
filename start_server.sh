#!/bin/sh

set -e

echo "=== Starting Listelog server ==="
cd server
docker compose up -d
cd ..

echo "Waiting for server..."
sleep 5

echo "=== Starting SPKEMB worker ==="
cd workers/spkemb
docker compose up -d
cd ../..

echo "=== Starting STTD-MOSS worker ==="
cd workers/sttd-moss
docker compose up -d
cd ../..

echo "=== All services started ==="
docker compose -f server/docker-compose.yml ps
docker compose -f workers/spkemb/docker-compose.yml ps
docker compose -f workers/sttd-moss/docker-compose.yml ps