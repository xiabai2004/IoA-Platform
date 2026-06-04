"""UCB1 Multi-Armed Bandit — 在线学习路由权重

在现有路由评分之上叠加 UCB 探索加分，使路由权重从经验值逐步收敛到数据驱动。

原理：
- 每个 Agent 是一个"臂"，修复成功 = reward 1，失败 = reward 0
- UCB1 公式：score = mean_reward + c * sqrt(ln(N) / n)
  - mean_reward: 历史平均成功率（exploit）
  - bonus: 探索加分，试得越少加分越高（explore）
- 最终路由分 = 0.85 × base_score + 0.15 × ucb_score

用法：
    bandit = BanditScorer()
    # 路由时
    final = bandit.blend(base_score, agent_id)
    # 验证完成后
    bandit.record(agent_id, reward=1.0)
    # 导出数据（答辩用）
    stats = bandit.export_stats()
"""
from __future__ import annotations

import math
import logging
import time
from typing import Any

logger = logging.getLogger("router.bandit")

# 融合权重：基础分 vs UCB 探索分
_BLEND_ALPHA = 0.15  # UCB 占比 15%


class BanditScorer:
    """UCB1 多臂老虎机——在线优化 Agent 选择权重。

    每个 (agent_id) 独立跟踪，不区分 capability/domain，
    因为同一 Agent 在不同上下文中的表现通常一致。
    """

    def __init__(self, c: float = 2.0):
        """
        Args:
            c: UCB 探索常数。c 越大越倾向探索（冒险），
               c 越小越倾向利用（保守）。默认 2.0 是标准值。
        """
        self.c = c
        # agent_id → {"n": int, "r": float, "last_seen": float}
        self._stats: dict[str, dict[str, Any]] = {}

    # ── 公开 API ──────────────────────────────────────────

    def ucb_score(self, agent_id: str) -> float:
        """计算 Agent 的 UCB1 分数。

        未探索过的 Agent 返回 1.0（最大探索加分），
        确保新注册的 Agent 能被尝试。
        """
        stats = self._stats.get(agent_id)
        if stats is None or stats["n"] == 0:
            return 1.0

        total_n = max(sum(s["n"] for s in self._stats.values()), 1)
        mean_reward = stats["r"] / stats["n"]
        exploration_bonus = self.c * math.sqrt(math.log(total_n) / stats["n"])
        return min(mean_reward + exploration_bonus, 1.0)

    def blend(self, base_score: float, agent_id: str) -> float:
        """将基础路由分与 UCB 探索分融合。

        Args:
            base_score: WeightedRouter / EmbeddingRouter 打出的基础分（0~1）
            agent_id:   目标 Agent ID

        Returns:
            融合后的最终分数（0~1）
        """
        ucb = self.ucb_score(agent_id)
        blended = (1.0 - _BLEND_ALPHA) * base_score + _BLEND_ALPHA * ucb
        return min(max(blended, 0.0), 1.0)

    def record(self, agent_id: str, reward: float) -> None:
        """记录一次路由结果的反馈。

        Args:
            agent_id: 被选中的 Agent
            reward:   0.0（失败）~ 1.0（成功），支持中间值
        """
        reward = max(0.0, min(1.0, float(reward)))
        if agent_id not in self._stats:
            self._stats[agent_id] = {"n": 0, "r": 0.0, "last_seen": time.time()}

        self._stats[agent_id]["n"] += 1
        self._stats[agent_id]["r"] += reward
        self._stats[agent_id]["last_seen"] = time.time()

        logger.debug(
            "Bandit: agent=%s reward=%.2f n=%d mean=%.3f",
            agent_id,
            reward,
            self._stats[agent_id]["n"],
            self._stats[agent_id]["r"] / self._stats[agent_id]["n"],
        )

    def export_stats(self) -> dict[str, Any]:
        """导出统计数据（答辩/可视化用）。

        Returns:
            {
                "agents": {
                    "monitor-east-china": {
                        "n": 12, "successes": 9, "failures": 3,
                        "mean_reward": 0.75, "ucb_score": 0.82
                    },
                    ...
                },
                "total_trials": 30,
                "c": 2.0,
                "blend_alpha": 0.15,
            }
        """
        total_n = sum(s["n"] for s in self._stats.values())
        agents = {}
        for aid, s in self._stats.items():
            mean = s["r"] / s["n"] if s["n"] > 0 else 0.0
            agents[aid] = {
                "n": s["n"],
                "successes": int(s["r"]),
                "failures": s["n"] - int(s["r"]),
                "mean_reward": round(mean, 4),
                "ucb_score": round(self.ucb_score(aid), 4),
                "last_seen": s.get("last_seen", 0),
            }

        return {
            "agents": agents,
            "total_trials": total_n,
            "c": self.c,
            "blend_alpha": _BLEND_ALPHA,
        }

    def convergence_curve(self) -> list[dict[str, float]]:
        """生成收敛曲线数据（答辩用）。

        返回按时间顺序的 (trial, mean_reward) 序列。
        注意：当前实现未保留历史时间序列，
        返回的是按 agent 聚合的累计均值。
        """
        points = []
        for aid, s in sorted(self._stats.items()):
            if s["n"] > 0:
                points.append({
                    "agent_id": aid,
                    "trials": s["n"],
                    "mean_reward": round(s["r"] / s["n"], 4),
                })
        return points

    def reset(self) -> None:
        """重置所有统计数据（调试用）。"""
        self._stats.clear()
        logger.info("BanditScorer reset")


# ── 全局单例 ──────────────────────────────────────────────

_bandit: BanditScorer | None = None


def get_bandit() -> BanditScorer:
    """获取全局 BanditScorer 单例。"""
    global _bandit
    if _bandit is None:
        _bandit = BanditScorer()
    return _bandit
