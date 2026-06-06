"""IoA 认证中间件 — Token 预共享密钥 (PSK) 验证

架构方案 v2 §3.2：Agent 与服务端通过预共享密钥进行 Token 认证。
- 请求头: Authorization: Bearer <pre_shared_key>
- 开放路径（无需认证）: /docs, /openapi.json, /health, /gui
- 注册中心: 读操作开放，写操作需要认证
"""

import json
import logging
import os
import secrets
from pathlib import Path
from fastapi import Request, HTTPException, status

logger = logging.getLogger("auth")

# Load .env before any config checks — parse directly to avoid dotenv ordering issues
def _load_env_file():
    """Parse .env file and load into os.environ. Searches cwd, parent, and backend/."""
    for p in [Path("."), Path(".."), Path("backend")]:
        env_path = p / ".env"
        if not env_path.exists():
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip("\"'")
                    if key and key not in os.environ:
                        os.environ[key] = val
            break
        except OSError:
            pass
_load_env_file()


# ── 预共享密钥加载 ────────────────────────────────────────

def _get_psk_unsafe(config: dict) -> str:
    """Get pre-shared key from environment or config. Rejects known weak defaults."""
    psk = os.environ.get("IOA_PSK") or config.get("auth", {}).get("pre_shared_key", "")

    if not psk:
        raise RuntimeError(
            "IOA_PSK not configured. Set the IOA_PSK environment variable "
            "or auth.pre_shared_key in config.yaml with a strong random key.\n"
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    WEAK_KEYS = {"ioa-dev-only-insecure-key", "ioa2026demo", "changeme", "admin", "password"}
    if psk.lower() in WEAK_KEYS or psk in WEAK_KEYS:
        raise RuntimeError(
            f"Insecure PSK detected. Please replace '{psk}' with a strong random key.\n"
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    return psk


def _load_psk() -> str:
    """从配置加载预共享密钥，拒绝弱密钥。认证关闭时跳过。"""
    # Check if auth is explicitly disabled — read .env directly to avoid import order issues
    import os as _os
    for _p in [Path("."), Path(".."), Path(__file__).parent.parent.parent]:
        _env = _p / ".env"
        if _env.exists():
            try:
                for _line in open(_env, encoding="utf-8"):
                    _line = _line.strip()
                    if _line.startswith("IOA_AUTH_ENABLED="):
                        if _line.split("=", 1)[1].strip().strip("\"'").lower() == "false":
                            return ""
            except OSError:
                pass
    if os.environ.get("IOA_AUTH_ENABLED", "true").lower() == "false":
        return ""
    from ioa_middleware.config import get_config
    config = get_config()
    return _get_psk_unsafe(config)

# 是否启用认证（必须在 _load_psk 之前检查）
_AUTH_ENABLED = os.environ.get("IOA_AUTH_ENABLED", "true").lower() != "false"

_PRE_SHARED_KEY = _load_psk()
if os.environ.get("IOA_ENV") == "production" and not _AUTH_ENABLED:
    raise RuntimeError("IOA_AUTH_ENABLED cannot be false in production mode")

# 无需认证的开放路径前缀
_OPEN_PREFIXES = (
    "/docs",
    "/openapi.json",
    "/redoc",
    "/health",
    "/gui",
    "/ws",
    "/messages/bandit",
    "/messages/reranker",
)

# 无需认证的精确路径
_OPEN_EXACT = {"/"}

# 注册中心只读路径（开放）
_REGISTRY_READ_PATHS = (
    "/registry/agents",
    "/registry/query",
)


def _is_open_path(path: str) -> bool:
    """判断是否为开放路径（无需认证）。"""
    if path in _OPEN_EXACT:
        return True
    return path.startswith(_OPEN_PREFIXES)


def _is_registry_read_path(path: str) -> bool:
    """判断是否为注册中心只读路径。"""
    return path in _REGISTRY_READ_PATHS


# ── FastAPI 中间件 ───────────────────────────────────────

class TokenAuthMiddleware:
    """Token 认证 ASGI 中间件。

    在请求进入路由前校验 Authorization 头，
    开放路径（/docs、/health 等）和 /registry 路径直接放行。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 认证已关闭 → 全部放行
        if not _AUTH_ENABLED:
            await self.app(scope, receive, send)
            return

        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # WebSocket 连接需要验证 token（开放路径除外）
        if scope["type"] == "websocket":
            # Dashboard WS 放行
            if path.startswith("/ws/dashboard"):
                await self.app(scope, receive, send)
                return
            # 从 query 参数中提取 token
            query_string = scope.get("query_string", b"").decode()
            params = dict(p.split("=") for p in query_string.split("&") if "=" in p)
            token = params.get("token", "")

            # 安全检查：必须提供有效 token
            if not token or not secrets.compare_digest(token, _PRE_SHARED_KEY):
                await self._unauthorized_ws(send, "Missing or invalid token")
                return

            await self.app(scope, receive, send)
            return

        # 开放路径直接放行
        if _is_open_path(path):
            await self.app(scope, receive, send)
            return

        # 注册中心只读路径放行
        if _is_registry_read_path(path):
            await self.app(scope, receive, send)
            return

        # 提取 Authorization 头
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()

        if not auth_header.startswith("Bearer "):
            await self._unauthorized(send, "Missing or invalid Authorization header")
            return

        token = auth_header[7:]  # 去掉 "Bearer " 前缀
        if not secrets.compare_digest(token, _PRE_SHARED_KEY):
            logger.warning("Invalid auth token attempt from %s", scope.get("client", ("unknown",))[0])
            await self._unauthorized(send, "Invalid token")
            return

        await self.app(scope, receive, send)

    async def _unauthorized(self, send, detail: str):
        """返回 401 Unauthorized。"""
        body = json.dumps({"detail": detail}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })

    async def _unauthorized_ws(self, send, detail: str):
        """返回 WebSocket 401 Unauthorized。"""
        await send({
            "type": "websocket.close",
            "code": 4001,  # 自定义关闭码
            "reason": detail,
        })


# ── 依赖注入（可选路由级别使用）──────────────────────────

async def verify_token(request: Request) -> str:
    """FastAPI 依赖：从请求中提取并校验 token。"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[7:]
    if not secrets.compare_digest(token, _PRE_SHARED_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token
