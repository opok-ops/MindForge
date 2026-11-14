"""
MindForge v5.0 类型定义
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


class PrivacyLevel(Enum):
    """隐私级别"""
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    PRIVATE = "PRIVATE"
    STRICT = "STRICT"

    @classmethod
    def from_string(cls, value: str) -> "PrivacyLevel":
        try:
            return cls(value.upper())
        except ValueError:
            return cls.INTERNAL

    def to_int(self) -> int:
        mapping = {"PUBLIC": 0, "INTERNAL": 1, "PRIVATE": 2, "STRICT": 3}
        return mapping[self.value]


class Importance(Enum):
    """重要性级别"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @classmethod
    def from_string(cls, value: str) -> "Importance":
        try:
            return cls(value.upper())
        except ValueError:
            return cls.MEDIUM

    def to_int(self) -> int:
        mapping = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        return mapping[self.value]


class MemoryType(Enum):
    """记忆类型（多模态）"""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    CODE = "code"
    STRUCTURED = "structured"
    MULTIMODAL = "multimodal"

    @classmethod
    def from_string(cls, value: str) -> "MemoryType":
        try:
            return cls(value.lower())
        except ValueError:
            return cls.TEXT


class MemoryLayer(Enum):
    """记忆层级（四层架构）"""
    SENSORY = "sensory"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    PERMANENT = "permanent"

    @classmethod
    def from_string(cls, value: str) -> "MemoryLayer":
        try:
            return cls(value.lower())
        except ValueError:
            return cls.SHORT_TERM

    def to_int(self) -> int:
        mapping = {"sensory": 0, "short_term": 1, "long_term": 2, "permanent": 3}
        return mapping[self.value]


@dataclass
class MemoryConfig:
    """记忆系统配置"""
    db_path: str = "./data/memory.db"
    key_file: str = "./data/.key"
    encrypted: bool = True
    default_privacy: PrivacyLevel = PrivacyLevel.INTERNAL
    default_importance: Importance = Importance.MEDIUM
    default_layer: MemoryLayer = MemoryLayer.SHORT_TERM
    auto_consolidate: bool = True
    # v5.7.9：add/update 后自动抽取实体/关系入知识图谱
    # v6.5.7 默认开启：图检索路（六路检索第 6 路）依赖 KG 数据，
    # 图空则该路空转——默认建图使六路开箱即用。正则抽取每条
    # 1-2ms 级，开销可忽略；显式置 False 可回到旧行为。
    auto_extract_graph: bool = True
    consolidate_interval_hours: int = 24
    forget_curve_enabled: bool = True
    vector_dim: int = 384
    max_short_term_items: int = 100
    sensory_buffer_size: int = 50
    sensory_retention_seconds: int = 30
    # v5.4.9 新增：Webhook / 事件通知配置
    webhooks: Optional[List[Dict[str, Any]]] = None
    webhook_secret: str = ""
    event_bus_enabled: bool = True


class DramaGenre(Enum):
    """短剧类型"""
    ROMANCE = "romance"
    SUSPENSE = "suspense"
    COMEDY = "comedy"
    ACTION = "action"
    HORROR = "horror"
    SCIFI = "scifi"
    FANTASY = "fantasy"
    DRAMA = "drama"
    OTHER = "other"

    @classmethod
    def from_string(cls, value: str) -> "DramaGenre":
        try:
            return cls(value.lower())
        except ValueError:
            return cls.OTHER


class DramaStatus(Enum):
    """短剧状态"""
    WATCHING = "watching"
    COMPLETED = "completed"
    PLANNED = "planned"
    DROPPED = "dropped"

    @classmethod
    def from_string(cls, value: str) -> "DramaStatus":
        try:
            return cls(value.lower())
        except ValueError:
            return cls.PLANNED


@dataclass
class DramaSeries:
    """短剧系列"""
    id: str
    title: str
    genre: DramaGenre = DramaGenre.OTHER
    total_episodes: int = 0
    current_episode: int = 0
    status: DramaStatus = DramaStatus.PLANNED
    platform: str = ""
    rating: float = 0.0
    description: str = ""
    tags: List[str] = field(default_factory=list)
    cover_url: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_watched_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "genre": self.genre.value,
            "total_episodes": self.total_episodes,
            "current_episode": self.current_episode,
            "status": self.status.value,
            "platform": self.platform,
            "rating": self.rating,
            "description": self.description,
            "tags": self.tags,
            "cover_url": self.cover_url,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_watched_at": self.last_watched_at,
        }


@dataclass
class DramaScene:
    """短剧场次"""
    id: str
    drama_id: str
    episode: int
    scene_number: int
    title: str
    content: str = ""
    location: str = ""
    time_of_day: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "drama_id": self.drama_id,
            "episode": self.episode,
            "scene_number": self.scene_number,
            "title": self.title,
            "content": self.content,
            "location": self.location,
            "time_of_day": self.time_of_day,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass
class DramaCharacter:
    """短剧角色"""
    id: str
    drama_id: str
    name: str
    role: str = "supporting"
    actor: str = ""
    description: str = ""
    personality: str = ""
    avatar_url: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "drama_id": self.drama_id,
            "name": self.name,
            "role": self.role,
            "actor": self.actor,
            "description": self.description,
            "personality": self.personality,
            "avatar_url": self.avatar_url,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass
class DramaLine:
    """短剧台词（可关联为记忆）"""
    id: str
    drama_id: str
    line_text: str
    scene_id: str = ""
    character_id: str = ""
    character_name: str = ""
    context: str = ""
    episode: int = 0
    timestamp: str = ""
    is_classic: bool = False
    memory_id: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "drama_id": self.drama_id,
            "scene_id": self.scene_id,
            "character_id": self.character_id,
            "character_name": self.character_name,
            "line_text": self.line_text,
            "context": self.context,
            "episode": self.episode,
            "timestamp": self.timestamp,
            "is_classic": self.is_classic,
            "memory_id": self.memory_id,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# 入参归一助手（v6.1.2）
# ---------------------------------------------------------------------------

def coerce_memory_id(value: Any) -> Optional[str]:
    """把 memory_id 入参归一为字符串，识别失败返回 None。

    为什么需要它：`mf.add()` 返回的是 `MemoryEntry` 对象，用户很自然地把它原样
    传回其它接口（如 `list_evolutions(m.add(...))`）。历史上各处写的是
    `if not memory_id or len(memory_id) > 128`，对对象求 `len()` 直接抛
    `TypeError: object of type 'MemoryEntry' has no len()`——对外表现为崩溃。
    v6.1.1 已在三个入口（learn_from_outcome / follow_up_create / forget_explain）
    修过这类问题，但每个模块各留了一份私有副本；v6.1.2 把规则收敛到此处的唯一
    实现，供 storage 的演变/版本接口共用。

    接受：
      - `str`：原样返回；
      - 带 `id` 字符串属性的对象（`MemoryEntry`、`ExperienceCase` 等）：返回其 `id`。
    其余（int / list / dict / None / 空串 / 超长）一律返回 None，由调用方按各自的
    错误约定处理（返回 error dict 或空列表），不再抛 TypeError。
    """
    if isinstance(value, str):
        candidate = value
    else:
        try:
            inner = getattr(value, "id", None)
        except Exception:  # noqa: BLE001 病态对象（属性访问抛异常）与非法类型同等对待
            return None
        if not isinstance(inner, str):
            return None
        candidate = inner
    if not candidate or len(candidate) > 128:
        return None
    return candidate
