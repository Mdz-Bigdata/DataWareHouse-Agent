# syntax=docker/dockerfile:1

# ---- 构建阶段：编译 React SPA ----
FROM node:22-alpine AS build

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---- 运行阶段：Nginx 静态托管 + 同域反代 ----
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
