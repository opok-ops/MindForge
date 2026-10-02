"""
MindForge v5.0 多模态记忆模块
支持文本、图像、音频、代码、结构化数据
"""

import base64
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple


class MultimodalType(Enum):
    """多模态类型"""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    CODE = "code"
    STRUCTURED = "structured"
    MULTIMODAL = "multimodal"


@dataclass
class MultimodalContent:
    """多模态内容"""
    content_type: MultimodalType
    data: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    text_representation: str = ""

    def to_dict(self) -> dict:
        return {
            "content_type": self.content_type.value,
            "data": self.data,
            "metadata": self.metadata,
            "text_representation": self.text_representation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MultimodalContent":
        return cls(
            content_type=MultimodalType(data.get("content_type", "text")),
            data=data.get("data", ""),
            metadata=data.get("metadata", {}),
            text_representation=data.get("text_representation", ""),
        )


@dataclass
class ImageMetadata:
    """图像元数据"""
    width: int = 0
    height: int = 0
    format: str = ""
    size_bytes: int = 0
    caption: str = ""
    objects_detected: List[str] = field(default_factory=list)
    scene_description: str = ""

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class AudioMetadata:
    """音频元数据"""
    duration_seconds: float = 0.0
    format: str = ""
    sample_rate: int = 0
    transcript: str = ""
    speaker: str = ""
    language: str = ""

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class CodeMetadata:
    """代码元数据"""
    language: str = ""
    lines_of_code: int = 0
    function_count: int = 0
    class_count: int = 0
    purpose: str = ""
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__


class MultimodalMemory:
    """多模态记忆管理器"""

    def __init__(self, storage=None, index=None):
        self.storage = storage
        self.index = index
        self._type_stats: Dict[str, int] = {}

    def create_text_memory(self, text: str, **kwargs) -> MultimodalContent:
        """创建文本记忆"""
        return MultimodalContent(
            content_type=MultimodalType.TEXT,
            data=text,
            text_representation=text,
            metadata={"word_count": len(text.split()), "char_count": len(text)},
        )

    def create_image_memory(self,
                            image_data: bytes,
                            caption: str = "",
                            width: int = 0,
                            height: int = 0,
                            format: str = "",
                            objects: Optional[List[str]] = None) -> MultimodalContent:
        """创建图像记忆"""
        # v5.4.7 修复 L-9：限制图像大小（默认 50MB）
        MAX_IMAGE_SIZE = 50 * 1024 * 1024
        if len(image_data) > MAX_IMAGE_SIZE:
            raise ValueError(f"Image too large: {len(image_data)} bytes (max {MAX_IMAGE_SIZE})")
        encoded = base64.b64encode(image_data).decode()
        metadata = ImageMetadata(
            width=width,
            height=height,
            format=format,
            size_bytes=len(image_data),
            caption=caption,
            objects_detected=objects or [],
        )

        text_repr = f"[图像] {caption} "
        if objects:
            text_repr += f"包含：{', '.join(objects)}"

        return MultimodalContent(
            content_type=MultimodalType.IMAGE,
            data=encoded,
            metadata=metadata.to_dict(),
            text_representation=text_repr,
        )

    def create_audio_memory(self,
                            audio_data: bytes,
                            transcript: str = "",
                            duration: float = 0.0,
                            format: str = "",
                            speaker: str = "",
                            language: str = "") -> MultimodalContent:
        """创建音频记忆"""
        # v5.4.7 修复 L-9：限制音频大小（默认 100MB）
        MAX_AUDIO_SIZE = 100 * 1024 * 1024
        if len(audio_data) > MAX_AUDIO_SIZE:
            raise ValueError(f"Audio too large: {len(audio_data)} bytes (max {MAX_AUDIO_SIZE})")
        encoded = base64.b64encode(audio_data).decode()
        metadata = AudioMetadata(
            duration_seconds=duration,
            format=format,
            transcript=transcript,
            speaker=speaker,
            language=language,
        )

        text_repr = f"[音频] {speaker} 说：{transcript}" if transcript else "[音频]"

        return MultimodalContent(
            content_type=MultimodalType.AUDIO,
            data=encoded,
            metadata=metadata.to_dict(),
            text_representation=text_repr,
        )

    def create_code_memory(self,
                           code: str,
                           language: str = "",
                           purpose: str = "",
                           dependencies: Optional[List[str]] = None) -> MultimodalContent:
        """创建代码记忆"""
        lines = code.strip().split('\n')
        func_count = sum(1 for line in lines if line.strip().startswith('def ') or line.strip().startswith('function '))
        class_count = sum(1 for line in lines if line.strip().startswith('class '))

        metadata = CodeMetadata(
            language=language,
            lines_of_code=len(lines),
            function_count=func_count,
            class_count=class_count,
            purpose=purpose,
            dependencies=dependencies or [],
        )

        text_repr = f"[代码-{language}] {purpose}\n{code[:200]}"

        return MultimodalContent(
            content_type=MultimodalType.CODE,
            data=code,
            metadata=metadata.to_dict(),
            text_representation=text_repr,
        )

    def create_structured_memory(self,
                                  data: Dict,
                                  schema_name: str = "",
                                  description: str = "") -> MultimodalContent:
        """创建结构化数据记忆"""
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        text_repr = f"[结构化数据-{schema_name}] {description}\n键：{', '.join(data.keys())}"

        return MultimodalContent(
            content_type=MultimodalType.STRUCTURED,
            data=json_str,
            metadata={"schema": schema_name, "key_count": len(data)},
            text_representation=text_repr,
        )

    def create_multimodal_memory(self,
                                  contents: List[MultimodalContent],
                                  description: str = "") -> MultimodalContent:
        """创建复合多模态记忆"""
        all_text = "\n".join(c.text_representation for c in contents)
        combined_data = json.dumps([c.to_dict() for c in contents], ensure_ascii=False)

        return MultimodalContent(
            content_type=MultimodalType.MULTIMODAL,
            data=combined_data,
            metadata={
                "modality_count": len(contents),
                "modalities": [c.content_type.value for c in contents],
                "description": description,
            },
            text_representation=all_text,
        )

    def search_multimodal(self,
                           query: str,
                           content_types: Optional[List[MultimodalType]] = None,
                           limit: int = 10) -> List[Tuple[MultimodalContent, float]]:
        """跨模态搜索"""
        if not self.index:
            return []

        results = self.index.search(query, top_k=limit * 2)

        filtered = []
        for doc_id, score in results:
            if self.storage:
                entry = self.storage.get_memory(doc_id)
                if entry:
                    mtype = entry.memory_type
                    if content_types and mtype not in [t.value for t in content_types]:
                        continue
                    content = MultimodalContent(
                        content_type=MultimodalType(mtype),
                        data=entry.content,
                        text_representation=entry.content,
                    )
                    filtered.append((content, score))

        return filtered[:limit]

    def get_type_stats(self) -> Dict[str, int]:
        """获取各类型统计"""
        if not self.storage:
            return self._type_stats

        stats = self.storage.get_stats()
        return stats.get("by_type", {})

    @staticmethod
    def compute_similarity(content1: MultimodalContent, content2: MultimodalContent) -> float:
        """计算两个多模态内容的相似度"""
        text1 = content1.text_representation.lower()
        text2 = content2.text_representation.lower()

        words1 = set(text1.split())
        words2 = set(text2.split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union)
