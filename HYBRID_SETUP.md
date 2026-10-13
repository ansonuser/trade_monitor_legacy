# 混合方案快速指南

> ⚠️ **此文件已過期，方案已停用（2026-09-07）。**
> `docker-compose.yml` 裡的 `scanner` service 已註解停用，實務上曾造成
> Docker 版與本機版 scanner 同時寫入同一個 Redis、互相覆蓋資料的問題
> （詳見 [README.md](README.md) 常見問題章節）。
> 目前唯一支援的正式方式請改用 `run_local.ps1`（scanner + taifex_fetcher +
> morning_runner + GUI 全部本機一起啟動，Docker 僅跑 Redis/InfluxDB/Grafana）。
> 本文件僅供歷史參考，請勿依此操作。

## 什么是混合方案？

```
┌─────────────────────────────────────────┐
│      Windows 本地                       │
│                                         │
│  tkinter GUI ◄──────┐                 │
│  (本地渲染、快速)   │ TCP 通信(1-5ms) │
│                    │                 │
└────────────────────┼─────────────────┘
                     │
                ┌────▼──────────────────┐
                │  Docker 容器          │
                │                       │
                │  Scanner ──► Redis   │
                │  (持续扫描)  (消息队列) │
                │                       │
                └───────────────────────┘
```

**优势：**
- ✅ tkinter GUI 开发快、本地即时反馈
- ✅ Scanner 独立在 Docker，随时可重启
- ✅ 完全适配 Windows + Docker 混合环境
- ✅ 性能最好：GUI 响应 < 10ms
- ✅ 极简配置：只需 Redis

---

## 前置准备

### 1. 本地虚拟环境

```powershell
# 建立虚拟环境
python -m venv .venv

# 啟動虛擬環境
.\.venv\Scripts\Activate.ps1

# 安装依赖
pip install -e .
```

### 2. Docker 已安装

确保 Docker Desktop 正在运行。

---

## 快速启动（3 種方式）

### 方式 1：一键启动脚本（推荐）

**PowerShell:**
```powershell
.\.run_hybrid.ps1
```

**或 Batch:**
```batch
run_hybrid.bat
```

### 方式 2：手动启动

**Step 1: 启动 Docker 服务**
```powershell
docker-compose up -d redis scanner
```

**Step 2: 在另一个终端启动 GUI**
```powershell
.\.venv\Scripts\Activate.ps1
python monitor/gui.py --interval 60
```

### 方式 3：查看日志

```powershell
# 查看 Scanner 日志
docker-compose logs -f scanner

# 查看 Redis 状态
docker-compose logs redis

# 查看所有服务
docker-compose ps
```

---

## Docker 命令参考

```powershell
# 查看服务状态
docker-compose ps

# 查看 Scanner 日志
docker-compose logs -f scanner

# 查看 Redis 日志
docker-compose logs redis

# 停止所有服务
docker-compose down

# 重启 Scanner
docker-compose restart scanner

# 清空 Redis 数据
docker-compose exec redis redis-cli FLUSHALL
```

---

## 参数说明

### GUI 参数

```powershell
python monitor/gui.py [OPTIONS]

Options:
  --interval N           更新间隔（秒），默认 60
  --redis-host HOST      Redis 主机，默认 127.0.0.1
  --redis-port PORT      Redis 埠，默认 6379
  --redis-db DB          Redis 数据库，默认 0

# 例子：
python monitor/gui.py --interval 30
```

### docker-compose 环境变量

在 `.env` 文件中配置：

```
SCANNER_INTERVAL=60          # Scanner 扫描间隔（秒）
REDIS_PORT=6379             # Redis 埠
REDIS_DB=0                  # Redis 数据库
```

---

## 常见问题

### Q: Redis 连接失败？

**A:** 检查 Redis 是否运行：

```powershell
docker-compose ps redis
docker-compose logs redis
```

确保本地 6379 端口未被占用：
```powershell
netstat -ano | findstr :6379
```

---

### Q: Scanner 无法启动？

**A:** 查看 Scanner 日志：

```powershell
docker-compose logs scanner
```

常见原因：
- 网络不可达 mis.twse.com.tw
- Redis 连接失败
- 股票清单获取超时

---

### Q: GUI 显示"等待扫描器数据..."

**A:** Scanner 正在初始化或网络延迟。

等待 30 秒左右 Scanner 第一次扫描完成。

查看 Scanner 日志：
```powershell
docker-compose logs -f scanner
```

---

### Q: 如何在非营业时段测试？

**A:** 编辑 `monitor/scanner.py`，注释掉营业时间检查：

```python
# def is_market_hours() -> bool:
#     now = datetime.datetime.now().time()
#     return MARKET_OPEN <= now <= MARKET_CLOSE

def is_market_hours() -> bool:
    return True  # 总是返回 True（用于测试）
```

或改成全天模式：
```powershell
python monitor/scanner.py --once  # 单次扫描
```

---

### Q: 如何提高 Scanner 扫描频率？

**A:** 在 `.env` 修改间隔：

```
SCANNER_INTERVAL=30          # 改成 30 秒
```

然后重启：
```powershell
docker-compose restart scanner
```

---

## 性能优化

| 参数 | 推荐值 | 说明 |
|------|-------|------|
| SCANNER_INTERVAL | 30-60s | 太快可能被台交所限流 |
| GUI --interval | 30-60s | GUI 更新频率 |
| Redis 连接 | 本地 | 远程会增加延迟 |

---

## 系统架构细节

### 数据流

```
1. Docker Scanner 每 60s 执行一次：
   - 获取全市场股票清单
   - 向 mis.twse.com.tw 并行请求快照
   - 计算资金流估算
   - 将结果写入 Redis:
     * tracker:stocks     → 个股数据 (JSON)
     * tracker:sectors    → 族群数据 (JSON)
     * tracker:timestamp  → 更新时间

2. Windows GUI 每 60s 读取一次：
   - 连接 Redis (1-5ms)
   - 拉取 tracker:stocks & tracker:sectors
   - 绘制 K线图 + 成交量
   - 显示个股 Top 20

3. 非营业时段：
   - Scanner 暂停（节省 CPU）
   - GUI 显示"等待开盘"
```

### Redis 数据格式

```json
// tracker:stocks
[
  {
    "sid": "2330",
    "name": "台積電",
    "industry": "22",
    "price": 580.5,
    "volume_total": 125000,
    "delta_vol": 8000,
    "flow_in": 4640000000,
    "flow_out": 2110000000,
    "net_flow": 2530000000,
    "chg_pct": 0.54,
    "day_chg_pct": 0.54
  },
  ...
]

// tracker:sectors
[
  {
    "industry_code": "22",
    "industry": "半導體",
    "open_chg_pct": 0.15,
    "avg_chg_pct": 0.45,
    "volume_total": 8950000,
    "flow_in": 12850000000,
    "flow_out": 5320000000,
    "net_flow": 7530000000,
    "delta_vol": 450000,
    "count": 28
  },
  ...
]
```

---

## 疑难排解

### 日志位置

```
logs/scanner_YYYYMMDD.log    # Scanner 日志
docker logs monitor_scanner  # Docker 日志
```

### 重置所有数据

```powershell
# 停止并删除容器 + 数据卷
docker-compose down -v

# 重新启动
docker-compose up -d redis scanner
```

---

## 下一步

- 📖 [查看 README.md](README.md) 了解项目概览
- 🔧 [查看 Dockerfile](Dockerfile) 了解容器配置
