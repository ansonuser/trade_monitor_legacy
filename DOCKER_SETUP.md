# Docker 部署指南

此指南说明如何在 Docker 中运行本项目的 Strategy Brain 服务。

## 项目架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Windows 本地环境                          │
│                                                             │
│  Windows Agent (scripts/)                                  │
│  ├─ 群益 SKCOM API 集成                                    │
│  └─ 实时行情推送到 Redis                                  │
│          │                                                 │
│          └──→ Redis 连接 ────────────────┐               │
│                                          │               │
└──────────────────────────────────────────┼───────────────┘
                                           │
                            ┌──────────────┘
                            │
┌──────────────────────────┬▼────────────────────────────────┐
│      Docker 容器环境      │                                │
│                          │                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Strategy Brain (src/tracker/)                       │ │
│  │ ├─ Redis 订阅模块                                  │ │
│  │ ├─ 策略计算引擎                                    │ │
│  │ └─ 订单执行模块                                    │ │
│  └─────────────────────────────────────────────────────┘ │
│     │                              │                     │
│     ▼                              ▼                     │
│  ┌─────────────────┐       ┌──────────────────┐        │
│  │  PostgreSQL     │       │  Redis           │        │
│  │  历史数据存储   │       │  消息队列        │        │
│  └─────────────────┘       └──────────────────┘        │
└───────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 准备配置文件

```bash
# 复制示例配置文件
cp .env.example .env

# 编辑 .env 文件（根据需要修改凭证）
# - SK_USER_ID: 群益证券用户ID
# - SK_PASSWORD: 群益证券密码
# - POSTGRES_PASSWORD: 数据库密码（建议修改）
```

### 2. 启动所有服务

```bash
# 构建镜像并启动容器
docker-compose up -d

# 查看日志
docker-compose logs -f

# 查看特定服务的日志
docker-compose logs -f app
docker-compose logs -f postgres
docker-compose logs -f redis
```

### 3. 初始化数据库（首次运行）

```bash
# 进入应用容器
docker-compose exec app bash

# 运行数据库初始化脚本
python scripts/init_db.py

# 运行数据获取脚本
python scripts/fetch_initial_data.py
```

### 4. 停止服务

```bash
# 停止所有容器
docker-compose down

# 停止并删除数据卷（删除数据库数据）
docker-compose down -v
```

## 常用命令

```bash
# 查看运行中的容器
docker-compose ps

# 重启特定服务
docker-compose restart app

# 查看容器内的日志
docker-compose logs app --tail=100

# 进入容器交互式shell
docker-compose exec app bash

# 运行数据库迁移
docker-compose exec app alembic upgrade head

# 检查服务健康状态
docker-compose ps  # STATUS 列会显示健康状态
```

## 服务端口映射

| 服务 | 容器内端口 | 主机端口 | 用途 |
|------|----------|--------|------|
| PostgreSQL | 5432 | 15432 | 数据库访问 |
| Redis | 6379 | 6379 | 消息队列访问 |
| Strategy Brain | 8000 | 8000 | API（如果启用） |

## 常见问题

### 1. PostgreSQL 连接失败

检查 PostgreSQL 容器是否健康：
```bash
docker-compose ps postgres
```

如果状态显示不健康，查看日志：
```bash
docker-compose logs postgres
```

### 2. Redis 连接失败

验证 Redis 是否正确启动：
```bash
docker-compose exec redis redis-cli ping
```

### 3. 应用无法启动

查看应用日志：
```bash
docker-compose logs app
```

检查依赖项是否正确安装：
```bash
docker-compose exec app pip list | grep -E "redis|psycopg|sqlalchemy"
```

### 4. Windows Agent 与 Docker 的 Redis 通信

确保 `.env` 中的 `REDIS_HOST` 设置正确：
- Docker 内部：使用 `redis`（服务名称）
- Windows 本地：使用 `localhost` 或 `127.0.0.1`
- 跨机器：使用实际的 IP 地址或域名

## 数据持久化

数据卷配置：
- `postgres_data`: PostgreSQL 数据文件（`/var/lib/postgresql/data`）
- `redis_data`: Redis 快照文件（`/data`）

数据会自动保存到 Docker 数据卷，即使容器停止也不会丢失。

## 环境变量说明

编辑 `.env` 文件配置以下变量：

| 变量 | 默认值 | 说明 |
|------|-------|------|
| REDIS_HOST | redis | Redis 主机名 |
| REDIS_PORT | 6379 | Redis 端口 |
| REDIS_DB | 0 | Redis 数据库序号 |
| POSTGRES_USER | tracker_user | 数据库用户 |
| POSTGRES_PASSWORD | tracker_password | 数据库密码 |
| POSTGRES_HOST | postgres | 数据库主机名 |
| POSTGRES_PORT | 5432 | 数据库端口 |
| POSTGRES_DB | tracker_db | 数据库名称 |
| APP_PORT | 8000 | 应用 API 端口 |
| SK_USER_ID | - | 群益证券用户 ID |
| SK_PASSWORD | - | 群益证券密码 |

## 相关文件

- `Dockerfile`: 应用镜像定义
- `docker-compose.yml`: 容器编排配置
- `.env.example`: 环境变量示例
- `.dockerignore`: Docker 构建忽略清单
