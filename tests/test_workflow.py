"""LangGraph 工作流测试"""

import pytest
import asyncio
from agents.orchestrator_agent.workflow import run_orchestrator_workflow


class TestOrchestratorWorkflow:
    """Orchestrator LangGraph 工作流测试"""

    @pytest.mark.asyncio
    async def test_workflow_basic_execution(self):
        """测试工作流基本执行"""
        result = await run_orchestrator_workflow(
            user_input="华东地区网络拥塞，请诊断修复",
        )
        
        assert "dag_definition" in result
        assert "template_name" in result
        assert "intent" in result
        assert "workflow_log" in result

    @pytest.mark.asyncio
    async def test_workflow_intent_parsing(self):
        """测试工作流意图解析"""
        result = await run_orchestrator_workflow(
            user_input="华北地区网络延迟异常",
        )
        
        intent = result.get("intent", {})
        assert "domain" in intent
        assert intent["domain"] == "north-china"

    @pytest.mark.asyncio
    async def test_workflow_template_selection(self):
        """测试工作流模板选择"""
        result = await run_orchestrator_workflow(
            user_input="全流程诊断修复华南地区网络",
        )
        
        assert result["template_name"] == "full_remediation"

    @pytest.mark.asyncio
    async def test_workflow_dag_generation(self):
        """测试工作流 DAG 生成"""
        result = await run_orchestrator_workflow(
            user_input="华东地区网络拥塞，请全流程诊断修复",
        )
        
        dag_def = result.get("dag_definition", {})
        assert "dag_id" in dag_def
        assert "nodes" in dag_def
        assert len(dag_def["nodes"]) > 0

    @pytest.mark.asyncio
    async def test_workflow_error_handling(self):
        """测试工作流错误处理"""
        # 空输入应该能处理
        result = await run_orchestrator_workflow(
            user_input="",
        )
        
        # 应该有错误或降级处理
        assert "errors" in result or "dag_definition" in result

    @pytest.mark.asyncio
    async def test_workflow_confidence_score(self):
        """测试工作流置信度评分"""
        result = await run_orchestrator_workflow(
            user_input="华东地区网络拥塞，请全流程诊断修复",
        )
        
        confidence = result.get("confidence", 0)
        assert 0 <= confidence <= 1

    @pytest.mark.asyncio
    async def test_workflow_log_messages(self):
        """测试工作流日志消息"""
        result = await run_orchestrator_workflow(
            user_input="华东地区网络拥塞",
        )
        
        log = result.get("workflow_log", [])
        assert len(log) > 0
        # 检查日志包含关键步骤
        log_text = " ".join(log)
        assert "意图解析" in log_text or "模板匹配" in log_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
