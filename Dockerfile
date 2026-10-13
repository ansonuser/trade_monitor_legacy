# 使用Python 3.12官方镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装 scanner 运行所需依赖
RUN pip install --no-cache-dir redis influxdb-client lxml curl_cffi

# 复制项目文件
COPY monitor ./monitor

# 创建非root用户运行应用
RUN useradd -m -u 1000 tracker && chown -R tracker:tracker /app
USER tracker

# 默认启动命令（docker-compose 中会以 command 覆盖）
CMD ["python", "monitor/scanner.py"]
