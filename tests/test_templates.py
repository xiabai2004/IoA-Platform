"""DAG 模板匹配测试"""

import pytest
from ioa_middleware.orchestrator.templates import match_template, TEMPLATES


class TestTemplateMatching:
    """DAG 模板匹配测试"""

    def test_match_full_remediation_chinese(self):
        """测试中文全流程修复模板匹配"""
        template_name, template_meta, score = match_template("华东地区网络拥塞，请全流程诊断修复")
        assert template_name == "full_remediation"
        assert score > 0.0

    def test_match_full_remediation_english(self):
        """测试英文全流程修复模板匹配"""
        template_name, template_meta, score = match_template("Network congestion in east-china, full remediation needed")
        assert template_name == "full_remediation"
        assert score > 0.0

    def test_match_monitor_only(self):
        """测试仅监控模板匹配"""
        template_name, template_meta, score = match_template("请监控华东地区网络状态")
        # 应该匹配到监控相关模板
        assert template_name in ["monitor_only", "full_remediation"]

    def test_match_diagnose_only(self):
        """测试仅诊断模板匹配"""
        template_name, template_meta, score = match_template("请诊断华北地区网络问题")
        # 应该匹配到诊断相关模板
        assert template_name in ["diagnose", "full_remediation"]

    def test_templates_have_required_fields(self):
        """测试模板包含必要字段"""
        for name, meta in TEMPLATES.items():
            assert "keywords" in meta, f"Template {name} missing 'keywords'"
            assert "description" in meta, f"Template {name} missing 'description'"
            assert "fn" in meta, f"Template {name} missing 'fn'"
            assert callable(meta["fn"]), f"Template {name} 'fn' is not callable"

    def test_template_function_generates_valid_dag(self):
        """测试模板函数生成有效的 DAG 定义"""
        for name, meta in TEMPLATES.items():
            dag_def = meta["fn"]({"domain": "east-china"})
            assert "dag_id" in dag_def, f"Template {name} DAG missing 'dag_id'"
            assert "nodes" in dag_def, f"Template {name} DAG missing 'nodes'"
            assert isinstance(dag_def["nodes"], list), f"Template {name} 'nodes' is not a list"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
