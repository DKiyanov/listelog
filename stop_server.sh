#!/bin/sh

set -e

echo "=== Stopping Listelog server ==="
cd server
docker compose stop
cd ..

echo "=== Stopping SPKEMB worker ==="
cd workers/spkemb
docker compose stop
cd ../..

echo "=== Stopping STTD-MOSS worker ==="
cd workers/sttd-moss
docker compose stop
cd ../..

echo "=== All Listelog containers stopped ==="