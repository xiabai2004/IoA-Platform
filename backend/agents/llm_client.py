"""LLM 客户端 — DeepSeek API 封装

使用 OpenAI 兼容接口（langchain-openai），无有效 API key 时自动降级为规则模式。

用法：
    client = get_llm_client(config)
    answer = await client.ask("分析以下网络指标...")
"""

import os
import logging

logger = logging.getLogger("llm_client")

# Provider 默认配置
PROVIDER_DEFAULTS = {
    "deepseek": {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
}


class LLMClient:
    """LLM 调用客户端 — DeepSeek API。

    无 API key 时自动降级为规则模式。
    """

    def __init__(self, config: dict):
        llm_cfg = config.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")

        # 读取 provider 专属配置段，其次取 provider 名对应的 key
        provider_cfg = llm_cfg.get(provider, {})
        defaults = PROVIDER_DEFAULTS.get(provider, {})

        self._model = provider_cfg.get("model") or defaults.get("model", "deepseek-chat")
        self._base_url = provider_cfg.get("base_url") or defaults.get("base_url", "https://api.deepseek.com/v1")
        self._provider = provider

        # API key: 先读配置，再读环境变量
        api_key_raw = provider_cfg.get("api_key", "")
        if api_key_raw.startswith("${") and api_key_raw.endswith("}"):
            env_var = api_key_raw[2:-1]
            api_key_raw = os.environ.get(env_var, "")
        if not api_key_raw:
            api_key_raw = os.environ.get(defaults.get("api_key_env", ""), "")

        self._api_key = api_key_raw
        self._llm = None

        if self._api_key:
            try:
                from langchain_openai import ChatOpenAI
                self._llm = ChatOpenAI(
                    model=self._model,
                    api_key=self._api_key,
                    base_url=self._base_url,
                    temperature=0.1,
                    max_tokens=1024,
                    request_timeout=15,
                    max_retries=1,
                )
                logger.info("LLM client initialized: provider=%s model=%s", provider, self._model)
            except Exception as e:
                logger.warning("Failed to init LLM (%s): %s, falling back to rule mode", provider, e)
        else:
            logger.info("No API key for provider=%s (set env %s), using rule-based mode",
                        provider, defaults.get("api_key_env", "API_KEY"))

    @property
    def available(self) -> bool:
        """LLM 是否可用。"""
        return self._llm is not None

    async def ask(self, prompt: str) -> str:
        """调用 LLM 推理，返回文本回复。

        若 LLM 不可用，返回空字符串（调用方应降级处理）。
        """
        if not self._llm:
            return ""

        try:
            from langchain_core.messages import HumanMessage
            response = await self._llm.ainvoke([HumanMessage(content=prompt)])
            return response.content.strip()
        except Exception as e:
            logger.warning("LLM call failed (%s): %s", self._provider, e)
            return ""


# ── 全局单例 ──────────────────────────────────────────────

_llm_client: LLMClient | None = None


def get_llm_client(config: dict | None = None) -> LLMClient:
    """获取 LLM 客户端单例。

    首次调用时自动从配置加载。
    """
    global _llm_client
    if _llm_client is None:
        if config is None:
            from ioa_middleware.config import get_config
            config = get_config()
        _llm_client = LLMClient(config)
    return _llm_client
