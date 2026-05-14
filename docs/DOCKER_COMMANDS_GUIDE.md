# Docker 开发环境常用命令指南

本文档总结了在 We-MP-RSS 项目开发过程中常用的 Docker 命令和故障排查方法。

## 1. 环境准备

### 1.1 创建必要的环境文件

```bash
# 在项目根目录下创建 .env 文件（docker-compose 需要）
touch .env

# 或直接创建包含必要配置的文件
cat > .env << EOF
# Dummy env file for docker-compose
USERNAME=admin
PASSWORD=admin@123
TZ=Asia/Shanghai
AUTO_RELOAD=True
PROXY_ENABLED=False
EOF
```

### 1.2 启动 Docker Desktop（macOS）

```bash
# 方法1：通过命令行启动
open -a Docker

# 方法2：手动启动 Docker Desktop 应用
# 从 macOS 启动台或应用程序文件夹中找到 Docker 图标点击启动
```

## 2. 容器管理基础命令

### 2.1 查看容器状态

```bash
# 查看所有容器（包括已停止的）
docker ps -a

# 查看运行中的容器
docker ps

# 查看与 we-mp-rss 相关的容器
docker ps -a | grep we-mp-rss
```

### 2.2 启动和停止容器

```bash
# 停止容器
docker stop <container_name_or_id>

# 启动已停止的容器
docker start <container_name_or_id>

# 重启容器
docker restart <container_name_or_id>

# 示例：重启前端容器
docker restart we-mp-rss-frontend-dev

# 示例：重启后端容器
docker restart we-mp-rss-backend-dev
```

### 2.3 删除容器

```bash
# 删除已停止的容器
docker rm <container_name_or_id>

# 强制删除运行中的容器
docker rm -f <container_name_or_id>

# 示例：删除并重建前端容器
docker stop we-mp-rss-frontend-dev
docker rm we-mp-rss-frontend-dev
```

## 3. Docker Compose 命令

本项目使用 `docker-compose.dev.yaml` 进行开发环境管理。

### 3.1 启动服务

```bash
# 启动所有服务（前台运行）
docker-compose -f compose/docker-compose.dev.yaml up

# 启动所有服务（后台运行）
docker-compose -f compose/docker-compose.dev.yaml up -d

# 启动指定服务（不启动依赖服务）
docker-compose -f compose/docker-compose.dev.yaml up -d frontend

# 示例：只启动前端和后端
docker-compose -f compose/docker-compose.dev.yaml up -d frontend backend
```

### 3.2 停止服务

```bash
# 停止所有服务
docker-compose -f compose/docker-compose.dev.yaml stop

# 停止并删除容器（保留数据卷）
docker-compose -f compose/docker-compose.dev.yaml down

# 停止并删除容器和数据卷
docker-compose -f compose/docker-compose.dev.yaml down -v
```

### 3.3 重建容器

```bash
# 强制重建指定服务（不启动依赖）
docker-compose -f compose/docker-compose.dev.yaml up -d --force-recreate --no-deps <service_name>

# 示例：强制重建前端容器
docker-compose -f compose/docker-compose.dev.yaml up -d --force-recreate --no-deps frontend

# 完整重建（删除旧容器并重新创建）
docker-compose -f compose/docker-compose.dev.yaml up -d --force-recreate
```

### 3.4 查看服务状态

```bash
# 查看运行状态
docker-compose -f compose/docker-compose.dev.yaml ps

# 查看服务日志
docker-compose -f compose/docker-compose.dev.yaml logs -f frontend

# 查看所有服务的实时日志
docker-compose -f compose/docker-compose.dev.yaml logs -f
```

## 4. 日志查看

```bash
# 查看容器日志（最后20行）
docker logs --tail=20 <container_name_or_id>

# 实时跟踪日志
docker logs -f <container_name_or_id>

# 查看指定时间段的日志
docker logs --since="2024-01-01T00:00:00" <container_name_or_id>

# 示例：查看前端容器日志
docker logs -f we-mp-rss-frontend-dev

# 示例：查看后端容器日志
docker logs -f we-mp-rss-backend-dev

# 示例：查看 Redis 日志
docker logs -f we-mp-rss-redis-dev
```

## 5. 端口检查

```bash
# 检查端口是否被占用
lsof -i :8001   # 检查后端端口
lsof -i :5173   # 检查前端端口
lsof -i :6379   # 检查 Redis 端口

# 示例输出
COMMAND     PID       USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
ClashX    42176 wangyicong   29u  IPv4  0x5c42605d4911f66c      0t0  TCP 192.168.31.110:53605->43.139.0.141:vcom-tunnel (ESTABLISHED)
```

## 6. 常见问题与解决方案

### 问题1：端口已被占用

**错误信息**：
```
Bind for 0.0.0.0:8001 failed: port is already allocated
```

**解决方案**：
```bash
# 1. 检查哪个容器占用了该端口
docker ps | grep 8001

# 2. 如果是旧容器，删除它
docker rm -f we-mp-rss-backend-dev

# 3. 或者停止占用端口的进程
lsof -i :8001 | awk 'NR>1 {print $2}' | xargs kill

# 4. 重新启动容器
docker-compose -f compose/docker-compose.dev.yaml up -d backend
```

### 问题2：容器无法启动

**错误信息**：
```
Cannot connect to the Docker daemon at unix:///Users/xxx/.docker/run/docker.sock
```

**解决方案**：
```bash
# 1. 启动 Docker Desktop
open -a Docker

# 2. 等待 Docker 启动完成
docker info

# 3. 如果仍然失败，重启 Docker Desktop
#    菜单栏 Docker 图标 -> Restart
```

### 问题3：代码修改没有生效

**原因**：Vite 热更新可能没有正常工作，或容器使用了旧代码。

**解决方案**：
```bash
# 方法1：重启容器
docker-compose -f compose/docker-compose.dev.yaml up -d --force-recreate frontend

# 方法2：完全重建
docker stop we-mp-rss-frontend-dev
docker rm we-mp-rss-frontend-dev
docker-compose -f compose/docker-compose.dev.yaml up -d frontend

# 方法3：清除浏览器缓存
# Mac: Cmd+Shift+R
# Windows: Ctrl+Shift+R
# 或打开开发者工具 -> Network 勾选 Disable cache -> 刷新
```

### 问题4：docker-compose 找不到 .env 文件

**错误信息**：
```
env file /path/to/.env not found
```

**解决方案**：
```bash
# 在项目根目录创建 .env 文件
touch .env

# 或使用模板创建
cp .env.example .env
```

### 问题5：容器一直重启或崩溃

**解决方案**：
```bash
# 1. 查看详细错误日志
docker logs --tail=100 <container_name>

# 2. 检查容器退出码
docker wait <container_name>

# 3. 以交互模式运行（调试用）
docker run -it --rm <image_name> /bin/sh
```

## 7. 完整开发工作流程

### 7.1 日常开发启动

```bash
# 1. 确保 Docker Desktop 运行
open -a Docker

# 2. 等待 Docker 启动
docker info

# 3. 启动所有开发服务
docker-compose -f compose/docker-compose.dev.yaml up -d

# 4. 检查服务状态
docker ps | grep we-mp-rss
```

### 7.2 修改代码后

```bash
# 1. 代码修改会自动同步（通过卷挂载）

# 2. 如果修改没有生效，重启前端容器
docker-compose -f compose/docker-compose.dev.yaml up -d --force-recreate frontend

# 3. 查看前端日志确认
docker logs -f we-mp-rss-frontend-dev
```

### 7.3 停止开发环境

```bash
# 停止所有服务（保留数据）
docker-compose -f compose/docker-compose.dev.yaml stop

# 或完全停止并删除容器
docker-compose -f compose/docker-compose.dev.yaml down
```

## 8. 项目相关容器说明

| 容器名称 | 描述 | 端口 | 重要文件挂载 |
|---------|------|------|------------|
| `we-mp-rss-backend-dev` | 后端服务 | 8001 | apis/, core/, driver/, jobs/ |
| `we-mp-rss-frontend-dev` | 前端开发服务器 | 5173 | web_ui/ |
| `we-mp-rss-redis-dev` | Redis 缓存 | 6379 | redis_data/ |
| `we-mp-rss-singbox-dev` | 代理服务 | - | - |

## 9. 快速命令参考

```bash
# 一键重启前端
docker-compose -f compose/docker-compose.dev.yaml up -d --force-recreate frontend

# 一键重启后端
docker-compose -f compose/docker-compose.dev.yaml up -d --force-recreate backend

# 查看所有日志
docker logs -f we-mp-rss-frontend-dev &
docker logs -f we-mp-rss-backend-dev &

# 检查端口占用
lsof -i :8001 -i :5173 -i :6379

# 快速状态检查
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

## 10. 注意事项

1. **数据持久化**：数据存储在 `./data` 目录，删除容器不会丢失数据
2. **环境变量**：`.env` 文件包含敏感信息，不要提交到版本控制
3. **端口冲突**：确保 8001、5173、6379 端口未被其他应用占用
4. **Docker Desktop**：确保 Docker Desktop 应用正在运行
5. **卷挂载**：开发模式下代码通过卷挂载同步，修改代码后容器内会自动更新

---

如遇到其他问题，请查看容器日志或参考项目 README 文档。
