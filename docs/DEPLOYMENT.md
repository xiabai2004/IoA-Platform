# 部署文档 — IoA 分布式网络运维协同平台

> 从零部署到 https://xiabai.site | 阿里云 ECS + Ubuntu 22.04 + systemd + Nginx

---

## 一、服务器要求

| 项目 | 最低配置 | 推荐配置 |
|------|----------|----------|
| 操作系统 | Ubuntu 20.04+ | Ubuntu 22.04 |
| CPU | 1核 | 2核 |
| 内存 | 1GB + 2GB swap | 2GB+ |
| 磁盘 | 10GB | 20GB |
| 网络 | 公网IP + 开放80/443端口 | 已备案域名 + SSL证书 |

当前部署环境：阿里云ECS ecs.e-c1m1.large（2核2G），公网IP 47.95.192.41，域名 xiabai.site。

---

## 二、环境准备

### 2.1 安装依赖

```bash
# 更新系统
apt update && apt upgrade -y

# 安装基础工具
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx

# 验证
python3 --version  # Python 3.10+
pip3 --version
```

### 2.2 创建 swap（内存不足时）

```bash
# 如果物理内存 < 2GB，创建 2GB swap
fallocate -l 2G /swapfile2
chmod 600 /swapfile2
mkswap /swapfile2
swapon /swapfile2

# 持久化
echo '/swapfile2 none swap sw 0 0' >> /etc/fstab
```

### 2.3 释放内存（可选）

如果服务器上有不必要的服务：

```bash
# 停掉不需要的服务（如 MySQL、宝塔面板）
systemctl stop mysql
systemctl disable mysql
systemctl stop bt
systemctl disable bt
```

---

## 三、部署步骤

### 3.1 上传项目

```bash
# 创建项目目录
mkdir -p /var/www/ioa-platform

# 上传后端代码（从本地）
scp -r backend/ root@47.95.192.41:/var/www/ioa-platform/
scp -r gui/ root@47.95.192.41:/var/www/ioa-platform/
scp requirements.txt root@47.95.192.41:/var/www/ioa-platform/
```

### 3.2 安装 Python 依赖

```bash
cd /var/www/ioa-platform/backend
pip3 install -r requirements.txt
```

requirements.txt 核心依赖：
```
fastapi>=0.100.0
uvicorn>=0.23.0
pydantic>=2.0.0
aiosqlite>=0.19.0
httpx>=0.24.0
```

### 3.3 创建 systemd 服务

#### 中间件 + Agent（端口 8000）

```bash
cat > /etc/systemd/system/ioa-platform.service << 'EOF'
[Unit]
Description=IoA Platform - Middleware + Agents
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/www/ioa-platform/backend
ExecStart=python3 -m uvicorn ioa_middleware.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
MemoryMax=800M
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

#### 网络模拟器（端口 8001）

```bash
cat > /etc/systemd/system/ioa-simulator.service << 'EOF'
[Unit]
Description=IoA Network Simulator
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/www/ioa-platform/backend
ExecStart=python3 -m uvicorn simulator.api:app --host 0.0.0.0 --port 8001
Restart=always
RestartSec=5
MemoryMax=300M
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

#### 启用并启动

```bash
systemctl daemon-reload
systemctl enable ioa-platform ioa-simulator
systemctl start ioa-platform ioa-simulator

# 验证
systemctl status ioa-platform ioa-simulator
ss -tlnp | grep -E '8000|8001'
```

---

## 四、Nginx 配置

### 4.1 创建站点配置

```bash
cat > /etc/nginx/sites-available/ioa << 'EOF'
server {
    listen 80;
    server_name xiabai.site;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name xiabai.site;

    ssl_certificate /etc/letsencrypt/live/xiabai.site/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/xiabai.site/privkey.pem;

    # IoA 中间件 + Agent API + GUI
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }

    # 网络模拟器 API
    location /sim/ {
        proxy_pass http://127.0.0.1:8001/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket（实时推送）
    location /ws/ {
        proxy_pass http://127.0.0.1:8000/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF
```

### 4.2 启用站点

```bash
ln -sf /etc/nginx/sites-available/ioa /etc/nginx/sites-enabled/ioa

# 删除默认站点（避免冲突）
rm -f /etc/nginx/sites-enabled/default

# 测试并重载
nginx -t
systemctl reload nginx
```

### 4.3 SSL 证书（首次部署）

```bash
certbot --nginx -d xiabai.site --non-interactive --agree-tos -m your-email@example.com
```

---

## 五、验证部署

### 5.1 检查服务状态

```bash
systemctl is-active ioa-platform ioa-simulator nginx
# 预期输出：三行 active

# 检查内存
free -h
# 预期：总内存 ~1.6G，已用 ~950M，可用 ~450M+
```

### 5.2 检查 Agent 在线

```bash
curl -s https://xiabai.site/registry/agents -H "Authorization: Bearer ioa2026demo" | python3 -m json.tool
```

预期输出 5 个 Agent，状态均为 "active"。

### 5.3 端到端测试

```bash
# 清除故障
curl -s https://xiabai.site/sim/simulator/fault/clear_all | python3 -m json.tool

# 注入链路拥塞故障
curl -s -X POST "https://xiabai.site/sim/simulator/fault/inject?fault_type=link_congestion&target=east-china" | python3 -m json.tool

# 发送 NL 指令触发 DAG
curl -s -X POST https://xiabai.site/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ioa2026demo" \
  -d '{"msg_id":"test-001","from_agent":"test","to_agent":"orchestrator-agent","intent":{"type":"user","description":"华东地区网络延迟异常，请全流程诊断修复"},"payload":{"params":{"message":"华东地区网络延迟异常，请全流程诊断修复"}},"correlation_id":"test-001","ts_ms":'$(date +%s000)'}' | python3 -m json.tool

# 等待 7 秒后查 DAG
sleep 7
curl -s https://xiabai.site/dag -H "Authorization: Bearer ioa2026demo" | python3 -m json.tool
```

### 5.4 GUI 可访问性

浏览器打开 https://xiabai.site/gui，确认：
- 拓扑图正常渲染（四域节点显示）
- 指标面板有实时数据
- Agent列表显示 5 个在线
- WebSocket 连接指示为绿色

---

## 六、常见问题

### Q1: 启动时端口被占用

```bash
# 查找并终止占用进程
ss -tlnp | grep 8000
kill -9 <PID>
```

### Q2: Agent 注册失败

Agent 启动时可能比 uvicorn 更早就绪，导致注册请求失败。`base_agent.py` 中已内置重试逻辑（最多10次，间隔1秒）。

如果仍然失败：
```bash
systemctl restart ioa-platform
```

### Q3: 内存不足 OOM

```bash
# 查看当前内存
free -h

# 临时方案：重启服务释放内存
systemctl restart ioa-platform ioa-simulator

# 长期方案：增加 swap 或升级服务器规格
```

### Q4: DeepSeek API 超时

diagnoser Agent 调用 LLM 推理时偶尔超时，当前使用规则引擎降级：
```python
# agents/diagnoser_agent.py
# LLM 超时时 fallback 到关键词匹配规则
```

正式部署建议配置 DeepSeek API Key 并设置合理超时（30s）。

### Q5: Nginx 502 Bad Gateway

```bash
# 检查后端服务是否运行
systemctl status ioa-platform ioa-simulator

# 查看错误日志
tail -f /var/log/nginx/error.log
journalctl -u ioa-platform -n 50
```

---

## 七、生产环境建议

1. **日志管理**：配置 logrotate 或接入日志平台
2. **监控告警**：接入 Prometheus + Grafana 监控服务指标
3. **自动重启**：systemd 已配置 `Restart=always`，服务异常退出自动恢复
4. **定期备份**：SQLite 数据文件 `/var/www/ioa-platform/backend/data/ioa.db` 定期备份
5. **安全加固**：配置 UFW 防火墙，只开放 80/443/22 端口
6. **密钥管理**：认证令牌和 API Key 使用环境变量，不硬编码