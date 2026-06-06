# IoA 分布式网络运维协同平台 — 部署指南

## 环境要求

| 项目 | 版本 |
|------|------|
| Python | ≥ 3.10 |
| 操作系统 | Windows / macOS / Linux |
| 网络 | 需访问 DeepSeek API 和 CDN（unpkg/jsdelivr） |

## 快速开始（3 步）

### Windows 用户

```
双击 setup.bat  →  等待安装完成  →  双击 run.bat
```

然后打开浏览器访问 **http://127.0.0.1:8000/gui**

### macOS / Linux 用户

```bash
chmod +x setup.sh
./setup.sh
source .venv/bin/activate
python backend/run.py
```

然后打开浏览器访问 **http://127.0.0.1:8000/gui**

---

## 手动部署（如果脚本失败）

```bash
# 1. 创建虚拟环境
python -m venv .venv

# 2. 激活虚拟环境
# Windows: .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate

# 3. 安装依赖
pip install -r backend/requirements.txt

# 4. 创建 .env 配置文件（填入你的 DeepSeek API Key）
echo DEEPSEEK_API_KEY=sk-your-key-here > .env
echo IOA_AUTH_ENABLED=false >> .env

# 5. 启动
python backend/run.py
```

---

## 配置说明

### DeepSeek API Key（必需）

`.env` 文件中必须配置 `DEEPSEEK_API_KEY`，用于 orchestrator 的语义路由意图解析。

获取方式：
1. 访问 https://platform.deepseek.com/api_keys
2. 注册并创建 API Key
3. 复制到 `.env` 中

### 认证（默认关闭）

演示/开发环境下认证默认关闭（`IOA_AUTH_ENABLED=false`），GUI 无需 token 即可访问所有接口。

生产环境建议开启：设置 `IOA_AUTH_ENABLED=true` 并配置 `IOA_PSK`。

### CDN 依赖

GUI 从 CDN 加载 vis-network 和 Chart.js：
- 首次加载需要网络连接（浏览器会缓存）
- 如果 CDN 不可用，拓扑图和图表会显示"加载失败"，其他功能正常

---

## 端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 8000 | IoA 中间件 | API + GUI + WebSocket |
| 8001 | 网络模拟器 | 指标生成 + 故障注入 |

---

## 常见问题

### pip install 失败

使用国内镜像：
```bash
pip install -r backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 端口被占用

修改 `backend/config.yaml` 中的端口，或关闭占用端口的程序。

### 拓扑图/图表显示加载失败

CDN 连接超时，刷新页面等待 CDN 加载完成即可。

---

## 提交工作流

```
1. 队友 fork 仓库
2. 按此指南部署
3. 录制演示视频时，用 .env 中的 DeepSeek Key 启动
4. 如有问题，群内沟通
```
