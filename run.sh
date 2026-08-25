#!/bin/bash
# 启动命令（每次都要用 --env-file 指定 backend/.env，否则 LOCAL_PAPER_LIBRARY_HOST_PATH 读不到）
docker compose --env-file backend/.env \
  -f docker-compose.yml \
  -f docker-compose.research.yml \
  -f docker-compose.local-library.yml \
  "$@"
