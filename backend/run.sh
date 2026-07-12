#!/bin/bash
# 启动 FastAPI 后端服务
# 激活虚拟环境
source ./venv/bin/activate
# 运行
PORT=8000 PYTHONPATH=. python app/main.py
