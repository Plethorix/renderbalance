#!/usr/bin/env bash
echo "Iniciando robot + API Flask en Render..."
exec gunicorn --bind 0.0.0.0:$PORT defi_robot:app
