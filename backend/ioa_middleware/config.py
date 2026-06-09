"""IoA 平台安全配置加载器

从环境变量和配置文件加载配置，支持环境变量优先级。
敏感信息（API密钥、认证令牌）必须通过环境变量提供。

使用方式：
    from ioa_middleware.config import get_config
    config = get_config()
    api_key = config["llm"]["deepseek"]["api_key"]
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

logger = logging.getLogger("config")

# 环境变量映射表
ENV_MAPPINGS = {
    "DEEPSEEK_API_KEY": ("llm", "deepseek", "api_key"),
    "IOA_PSK": ("auth", "pre_shared_key"),
    "MIDDLEWARE_PORT": ("middleware", "port"),
    "SIMULATOR_PORT": ("simulator", "port"),
    "DATABASE_PATH": ("database", "path"),
    "LOG_LEVEL": ("logging", "level"),
}

# 默认配置（不含敏感信息）
DEFAULT_CONFIG = {
    "llm": {
        "provider": "deepseek",
        "deepseek": {
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
        },
    },
    "middleware": {
        "host": "0.0.0.0",
        "port": 8000,
    },
    "simulator": {
        "host": "0.0.0.0",
        "port": 8001,
    },
    "database": {
        "path": "data/ioa.db",
    },
    "ws": {
        "metrics_interval_ms": 1000,
        "message_buffer_size": 50,
    },
    "simulator_config": {
        "domains": ["east-china", "north-china", "south-china", "west-china"],
        "update_interval_ms": 1000,
        "noise_level": 0.05,
    },
    "auth": {
        "mode": "token",
    },
    "bus": {
        "backend": "memory",
        "nats_servers": "nats://nats:4222",
    },
    "cors": {
        "allowed_origins": [
            "http://localhost:8000",
            "http://localhost:3000",
            "http://127.0.0.1:8000",
        ],
        "allowed_methods": ["GET", "POST", "PUT", "DELETE"],
        "allowed_headers": ["Authorization", "Content-Type"],
    },
}


def _set_nested(d: dict, keys: tuple, value: Any) -> None:
    """设置嵌套字典的值。"""
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def _resolve_env_vars(value: str) -> str:
    """解析字符串中的环境变量引用（${VAR_NAME}格式）。"""
    if not isinstance(value, str):
        return value

    if value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        env_value = os.environ.get(env_var)
        if env_value:
            return env_value
        logger.warning("Environment variable %s not set", env_var)
        return ""

    return value


def _apply_env_vars(config: dict) -> dict:
    """递归应用环境变量替换。"""
    if isinstance(config, dict):
        return {k: _apply_env_vars(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [_apply_env_vars(item) for item in config]
    elif isinstance(config, str):
        return _resolve_env_vars(config)
    return config


def _resolve_backend_dir() -> Path:
    """定位 backend/ 目录（基于当前文件位置或 CWD）。"""
    # 优先用 __file__ 定位（最可靠）
    here = Path(__file__).resolve().parent.parent  # config.py → ioa_middleware → backend
    if (here / "run.py").exists():
        return here
    # 回退：CWD 是否就是 backend/
    cwd = Path.cwd()
    if (cwd / "run.py").exists():
        return cwd
    # 再回退：CWD/backend/ 是否存在
    if (cwd / "backend" / "run.py").exists():
        return cwd / "backend"
    return here  # 最终回退


BACKEND_DIR = _resolve_backend_dir()


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件。

    优先级：环境变量 > 配置文件 > 默认值
    """
    # 0. 加载 .env 环境变量文件（必须优先于配置文件）
    # 搜索路径: backend/ → cwd
    _searched = []
    for _p in [BACKEND_DIR, Path(".")]:
        env_path = _p / ".env"
        _searched.append(str(env_path.resolve()))
        if env_path.exists():
            load_dotenv(env_path, override=False)
            logger.info("Loaded environment variables from %s", env_path)
            break
    else:
        logger.debug("No .env file found. Searched: %s", ", ".join(_searched))

    config = DEFAULT_CONFIG.copy()

    # 1. 尝试加载配置文件（backend/ → cwd）
    _config_file = None
    for _p in [BACKEND_DIR / config_path, Path(config_path)]:
        if _p.exists():
            _config_file = _p
            break
    if _config_file:
        try:
            with open(_config_file, encoding="utf-8") as f:
                file_config = yaml.safe_load(f)
                if file_config:
                    _deep_merge(config, file_config)
            logger.info("Loaded config from %s", _config_file)
        except Exception as e:
            logger.warning("Failed to load config file %s: %s", _config_file, e)
    else:
        logger.info("Config file %s not found (checked CWD and backend/), using defaults", config_path)

    # 2. 应用环境变量覆盖
    for env_var, key_path in ENV_MAPPINGS.items():
        env_value = os.environ.get(env_var)
        if env_value:
            _set_nested(config, key_path, env_value)
            logger.debug("Config %s overridden by env var %s", ".".join(key_path), env_var)

    # 3. 解析配置中的环境变量引用
    config = _apply_env_vars(config)

    # Bus defaults
    config.setdefault("bus", {})
    config["bus"].setdefault("backend", os.environ.get("IOA_BUS_BACKEND", "memory"))
    config["bus"].setdefault("nats_servers", os.environ.get("NATS_SERVERS", "nats://nats:4222"))

    # 4. 统一数据库路径：确保始终指向 backend/data/ioa.db
    db_path = config.get("database", {}).get("path", "data/ioa.db")
    if not Path(db_path).is_absolute():
        resolved = (BACKEND_DIR / db_path).resolve()
        config["database"]["path"] = str(resolved)
        logger.debug("Database path resolved: %s -> %s", db_path, resolved)

    # 5. 验证必需的敏感配置
    _validate_config(config)

    return config


def _deep_merge(base: dict, override: dict) -> None:
    """深度合并字典。"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _validate_config(config: dict) -> None:
    """验证配置的完整性和安全性。"""
    # 检查 API 密钥
    deepseek_key = config.get("llm", {}).get("deepseek", {}).get("api_key", "")
    if not deepseek_key:
        logger.warning(
            "DeepSeek API key not set. "
            "Set DEEPSEEK_API_KEY environment variable or configure in config.yaml. "
            "LLM features will be disabled."
        )

    # PSK — must be set via environment, no hardcoded default
    psk = os.environ.get("IOA_PSK")
    if psk:
        config["auth"]["pre_shared_key"] = psk
    elif not config.get("auth", {}).get("pre_shared_key"):
        config.setdefault("auth", {})
        config["auth"]["pre_shared_key"] = ""  # Empty → will trigger RuntimeError at auth check


# 全局配置单例
_config: dict | None = None


def get_config() -> dict:
    """获取全局配置单例。"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> dict:
    """重新加载配置。"""
    global _config
    _config = load_config()
    return _config
