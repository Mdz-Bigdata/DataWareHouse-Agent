#!/bin/bash

# =====================================================================
# 多Agent数仓开发与智能问数系统 - 一键启动脚本 (start.sh)
# 功能: 自动检测端口占用，在后台拉起后端 FastAPI 和前端 Vite，并在退出时优雅清理。
# =====================================================================

# 终端输出着色
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # 无颜色

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}          🚀 欢迎使用多Agent数仓与智能问数系统一键启动器 🚀${NC}"
echo -e "${BLUE}======================================================================${NC}"

# 检测 8000 端口占用
if lsof -i :8000 > /dev/null 2>&1; then
    echo -e "${YELLOW}[WARNING] 发现 8000 端口已被占用，正在尝试清理原有后端服务...${NC}"
    lsof -t -i :8000 | xargs kill -9 > /dev/null 2>&1
    sleep 1
fi

# 1. 启动后端 FastAPI
echo -e "${GREEN}[STEP 1] 正在启动后端 Python FastAPI 服务 (端口: 8000)...${NC}"
cd backend
if [ ! -d "venv" ]; then
    echo -e "${RED}[ERROR] 未检测到 python 虚拟环境 venv！请先运行初始化依赖安装。${NC}"
    exit 1
fi

# 激活虚拟环境并在后台启动
source venv/bin/activate
PORT=8000 PYTHONPATH=. python app/main.py > backend.log 2>&1 &
BACKEND_PID=$!
echo -e "${GREEN}✓ 后端已在后台启动 (PID: $BACKEND_PID)，日志输出至 backend/backend.log${NC}"
cd ..

# 等待后端完成初始化
sleep 2

# 2. 启动前端 Vite
echo -e "${GREEN}[STEP 2] 正在启动前端 React Vite 调试服务器...${NC}"
cd frontend
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}[WARNING] 未检测到 node_modules，正在自动为您安装前端依赖...${NC}"
    npm install
fi

# 在后台启动开发服务器
npm run dev > frontend.log 2>&1 &
FRONTEND_PID=$!
echo -e "${GREEN}✓ 前端已在后台启动 (PID: $FRONTEND_PID)，日志输出至 frontend/frontend.log${NC}"
cd ..

sleep 1
echo -e "${BLUE}======================================================================${NC}"
echo -e "${GREEN}🎉 系统启动成功！🎉${NC}"
echo -e "${GREEN}👉 前端访问地址: http://localhost:5173/ (若5173占用自动顺延至5174)${NC}"
echo -e "${GREEN}👉 后端 API 地址: http://localhost:8000/docs (Swagger 文档)${NC}"
echo -e "${YELLOW}提示: 按 [Ctrl + C] 可以一键安全退出并自动终止前后端进程。${NC}"
echo -e "${BLUE}======================================================================${NC}"

# 监听中断信号 (Ctrl+C)，优雅清理后台任务
cleanup() {
    echo -e "\n${YELLOW}正在清理后台服务进程...${NC}"
    if [ -n "$BACKEND_PID" ]; then
        kill $BACKEND_PID > /dev/null 2>&1
        echo -e "${BLUE}✓ 已终止后端进程 (PID: $BACKEND_PID)${NC}"
    fi
    if [ -n "$FRONTEND_PID" ]; then
        # Vite 可能会产生子树，使用 pkill 或 kill 进程组
        kill $FRONTEND_PID > /dev/null 2>&1
        echo -e "${BLUE}✓ 已终止前端进程 (PID: $FRONTEND_PID)${NC}"
    fi
    # 额外通过端口清理防止遗漏残留
    lsof -t -i :8000 | xargs kill -9 > /dev/null 2>&1
    echo -e "${GREEN}✨ 所有后台服务清理完毕，退出成功。${NC}"
    exit 0
}

trap cleanup INT

# 持续挂起并展示日志尾部 (模拟终端输出)
tail -f backend/backend.log
