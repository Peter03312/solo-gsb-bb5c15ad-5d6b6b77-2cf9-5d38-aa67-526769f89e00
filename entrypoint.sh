#!/bin/sh
set -e

# 初始化数据库（表不存在则建表；库为空且 SEED_DEMO=1 时写入演示数据）
python -m app.initdb

exec python -m gunicorn \
    --workers "${GUNICORN_WORKERS:-2}" \
    --bind "0.0.0.0:${PORT:-8000}" \
    --timeout 180 \
    'app:create_app()'
