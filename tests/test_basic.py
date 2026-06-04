"""简单测试 - 不依赖项目模块"""

import pytest


class TestBasicFunctionality:
    """基本功能测试"""

    def test_domain_extraction(self):
        """测试域名提取逻辑"""
        domain_aliases = {
            "华东": "east-china", "东部": "east-china", "east": "east-china",
            "华北": "north-china", "北部": "north-china", "north": "north-china",
            "华南": "south-china", "南部": "south-china", "south": "south-china",
            "西南": "west-china", "西部": "west-china", "west": "west-china",
        }
        
        def extract_domain(text):
            text_lower = text.lower()
            for alias, domain in domain_aliases.items():
                if alias in text_lower:
                    return domain
            return "east-china"
        
        assert extract_domain("华东地区网络拥塞") == "east-china"
        assert extract_domain("华北地区延迟异常") == "north-china"
        assert extract_domain("华南地区丢包严重") == "south-china"
        assert extract_domain("西南地区带宽不足") == "west-china"
        assert extract_domain("east-china network issue") == "east-china"
        assert extract_domain("unknown region") == "east-china"  # 默认

    def test_fault_type_extraction(self):
        """测试故障类型提取"""
        def extract_fault_type(text):
            text_lower = text.lower()
            if "拥塞" in text or "congestion" in text:
                return "link_congestion"
            elif "中断" in text or "outage" in text:
                return "link_outage"
            elif "cpu" in text_lower or "过载" in text:
                return "cpu_overload"
            elif "ddos" in text_lower or "攻击" in text:
                return "ddos"
            elif "配置" in text or "misconfig" in text:
                return "misconfig"
            return "unknown"
        
        assert extract_fault_type("华东地区网络拥塞") == "link_congestion"
        assert extract_fault_type("链路中断") == "link_outage"
        assert extract_fault_type("CPU过载") == "cpu_overload"
        assert extract_fault_type("DDoS攻击") == "ddos"
        assert extract_fault_type("配置错误") == "misconfig"
        assert extract_fault_type("其他问题") == "unknown"

    def test_confidence_score_range(self):
        """测试置信度评分范围"""
        def calculate_confidence(match_count, total_keywords):
            if total_keywords == 0:
                return 0.0
            return min(match_count / total_keywords, 1.0)
        
        assert calculate_confidence(3, 5) == 0.6
        assert calculate_confidence(5, 5) == 1.0
        assert calculate_confidence(0, 5) == 0.0
        assert calculate_confidence(10, 5) == 1.0  # 上限为 1.0
        assert calculate_confidence(0, 0) == 0.0  # 除零保护

    def test_dag_id_generation(self):
        """测试 DAG ID 生成"""
        import time
        
        def generate_dag_id(template_name):
            return f"dag-{template_name}-{int(time.time()*1000)}"
        
        dag_id = generate_dag_id("full_remediation")
        assert dag_id.startswith("dag-full_remediation-")
        assert len(dag_id) > 20
        
        # 确保唯一性
        dag_id2 = generate_dag_id("full_remediation")
        # 由于时间戳不同，ID 应该不同（除非在同一毫秒内）
        # 这里我们只检查格式
        assert dag_id2.startswith("dag-full_remediation-")

    def test_xss_escape(self):
        """测试 XSS 转义函数"""
        def esc(s):
            if not s:
                return ''
            d = __import__('html').escape(str(s))
            return d
        
        assert esc("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
        assert esc("normal text") == "normal text"
        assert esc("") == ""
        assert esc(None) == ""

    def test_template_matching_keywords(self):
        """测试模板关键词匹配"""
        templates = {
            "full_remediation": {
                "keywords": ["修复", "诊断", "全流程", "remediation", "fix"],
            },
            "monitor_only": {
                "keywords": ["监控", "检测", "monitor"],
            },
            "diagnose_only": {
                "keywords": ["诊断", "分析", "diagnose"],
            },
        }
        
        def match_template(user_input, templates):
            best_match = "full_remediation"
            best_score = 0.0
            
            for name, meta in templates.items():
                keywords = meta.get("keywords", [])
                score = sum(1 for kw in keywords if kw in user_input.lower())
                normalized_score = score / max(len(keywords), 1)
                if normalized_score > best_score:
                    best_score = normalized_score
                    best_match = name
            
            return best_match, best_score
        
        # 全流程修复
        name, score = match_template("华东地区网络拥塞，请全流程诊断修复", templates)
        assert name == "full_remediation"
        assert score > 0.0
        
        # 仅监控
        name, score = match_template("请监控华东地区网络状态", templates)
        assert name == "monitor_only"
        assert score > 0.0
        
        # 仅诊断
        name, score = match_template("请诊断华北地区网络问题", templates)
        assert name == "diagnose_only"
        assert score > 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
