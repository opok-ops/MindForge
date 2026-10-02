"""
MindForge v5.0 分类管理模块
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class CategoryNode:
    name: str
    parent: Optional[str] = None
    children: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    description: str = ""


DEFAULT_TAXONOMY = {
    "work": {
        "name": "工作",
        "keywords": ["工作", "项目", "任务", "会议", "报告", "同事", "老板", "公司", "业务"],
        "children": ["tech", "project", "meeting"],
    },
    "tech": {
        "name": "技术",
        "keywords": ["技术", "编程", "代码", "开发", "bug", "算法", "架构", "设计"],
        "parent": "work",
        "children": ["programming", "devops", "design"],
    },
    "programming": {
        "name": "编程开发",
        "keywords": ["python", "java", "javascript", "函数", "类", "接口", "api"],
        "parent": "tech",
    },
    "life": {
        "name": "生活",
        "keywords": ["生活", "吃饭", "睡觉", "家庭", "朋友", "旅行", "美食", "电影"],
        "children": ["health", "hobby"],
    },
    "health": {
        "name": "健康",
        "keywords": ["健康", "运动", "健身", "饮食", "睡眠", "体检", "疾病"],
        "parent": "life",
    },
    "learning": {
        "name": "学习",
        "keywords": ["学习", "研究", "阅读", "书籍", "课程", "笔记", "知识"],
        "children": [],
    },
    "idea": {
        "name": "创意",
        "keywords": ["创意", "想法", "灵感", "设计", "发明", "创新", "产品"],
        "children": [],
    },
    "personal": {
        "name": "个人",
        "keywords": ["我", "自己", "个人", "隐私", "秘密", "想法", "感受"],
        "children": [],
    },
}


class TaxonomyManager:
    """分类管理器"""

    def __init__(self, taxonomy: Optional[Dict] = None):
        self.taxonomy = taxonomy or DEFAULT_TAXONOMY
        self._build_keyword_index()

    def _build_keyword_index(self):
        self._keyword_map: Dict[str, str] = {}
        for cat_id, cat_data in self.taxonomy.items():
            for keyword in cat_data.get("keywords", []):
                self._keyword_map[keyword.lower()] = cat_id

    def suggest_category(self, content: str) -> str:
        """建议分类"""
        content_lower = content.lower()
        scores: Dict[str, int] = {}

        for keyword, cat_id in self._keyword_map.items():
            if keyword in content_lower:
                scores[cat_id] = scores.get(cat_id, 0) + 1

        if not scores:
            return "general"

        best_category = max(scores.items(), key=lambda x: x[1])[0]
        return best_category

    def suggest_tags(self, content: str, max_tags: int = 5) -> List[str]:
        """建议标签"""
        content_lower = content.lower()
        tags = []

        for keyword, cat_id in self._keyword_map.items():
            if keyword in content_lower:
                if keyword not in tags:
                    tags.append(keyword)

        words = re.findall(r'[\w\u4e00-\u9fff]+', content_lower)
        from collections import Counter
        word_counts = Counter(words)
        for word, count in word_counts.most_common(10):
            if len(word) > 2 and word not in tags and count >= 1:
                tags.append(word)
                if len(tags) >= max_tags:
                    break

        return tags[:max_tags]

    def get_category_info(self, category_id: str) -> Optional[Dict]:
        """获取分类信息"""
        return self.taxonomy.get(category_id)

    def get_all_categories(self) -> Dict[str, Dict]:
        """获取所有分类"""
        return self.taxonomy

    def get_subcategories(self, parent_id: str) -> List[str]:
        """获取子分类"""
        parent = self.taxonomy.get(parent_id)
        if parent:
            return parent.get("children", [])
        return []

    def get_parent_category(self, category_id: str) -> Optional[str]:
        """获取父分类"""
        cat = self.taxonomy.get(category_id)
        if cat:
            return cat.get("parent")
        return None

    def add_category(self, category_id: str, name: str,
                     keywords: Optional[List[str]] = None,
                     parent: Optional[str] = None):
        """添加分类"""
        self.taxonomy[category_id] = {
            "name": name,
            "keywords": keywords or [],
            "parent": parent,
            "children": [],
        }

        if parent and parent in self.taxonomy:
            if category_id not in self.taxonomy[parent]["children"]:
                self.taxonomy[parent]["children"].append(category_id)

        for keyword in keywords or []:
            self._keyword_map[keyword.lower()] = category_id
