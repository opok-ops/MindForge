"""
MindForge v5.5.8 嵌入引擎

多后端适配器架构（v5.4.6 新增）：
- sentence-transformers（本地 CPU 推理，默认）
- OpenAI Embedding API（需 API key，无 GPU 友好）
- Ollama（本地推理服务）
- 自定义 HTTP 端点

通过环境变量或构造参数配置后端：
  MINDFORGE_EMBEDDING_BACKEND=sentence_transformers|openai|ollama|http
  OPENAI_API_KEY=sk-...
  MINDFORGE_EMBEDDING_MODEL=text-embedding-3-small
  MINDFORGE_EMBEDDING_API_URL=https://custom.endpoint/embed
  OLLAMA_HOST=http://localhost:11434

默认模型: paraphrase-multilingual-MiniLM-L12-v2 (384 维, ~120MB, 支持50+语言)
"""

import struct
import logging
import os
import threading
import json
from typing import List, Optional, Tuple
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# 默认模型名
DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# 默认维度（all-MiniLM-L6-v2 输出 384 维）
DEFAULT_DIMENSION = 384


# ===== 后端适配器抽象基类 =====

class EmbeddingBackend(ABC):
    """嵌入后端抽象基类

    所有后端适配器需实现 encode / encode_batch / is_available / dimension / model_name。
    """

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """后端是否可用"""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """嵌入向量维度"""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称"""
        ...

    @abstractmethod
    def encode(self, text: str) -> Optional[List[float]]:
        """编码单条文本"""
        ...

    @abstractmethod
    def encode_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """批量编码文本"""
        ...


# ===== sentence-transformers 后端（默认） =====

class SentenceTransformerBackend(EmbeddingBackend):
    """sentence-transformers 本地推理后端

    可选依赖，未安装时 is_available 返回 False。
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self._model_name = model_name
        self._model = None
        self._dimension = DEFAULT_DIMENSION
        self._load_attempted = False
        self._available = False

    @property
    def is_available(self) -> bool:
        if not self._load_attempted:
            self._try_load()
        return self._available

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def _try_load(self):
        self._load_attempted = True
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            test_vec = self._model.encode("test", show_progress_bar=False)
            self._dimension = len(test_vec)
            self._available = True
            logger.info("SentenceTransformerBackend 加载成功: %s (dim=%d)",
                        self._model_name, self._dimension)
        except ImportError:
            logger.warning(
                "sentence-transformers 未安装，向量检索不可用。"
                "安装: pip install sentence-transformers")
            self._available = False
        except Exception as e:
            logger.warning("SentenceTransformerBackend 加载失败: %s", e)
            self._available = False

    def encode(self, text: str) -> Optional[List[float]]:
        if not self.is_available or not text:
            return None
        try:
            vec = self._model.encode(text, show_progress_bar=False,
                                     normalize_embeddings=True)
            return vec.tolist()
        except Exception as e:
            logger.warning("encode 失败: %s", e)
            return None

    def encode_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        if not self.is_available or not texts:
            return None
        try:
            vecs = self._model.encode(
                texts, show_progress_bar=False,
                normalize_embeddings=True, batch_size=32)
            return [v.tolist() for v in vecs]
        except Exception as e:
            logger.warning("encode_batch 失败: %s", e)
            return None


# ===== OpenAI Embedding 后端 =====

class OpenAIBackend(EmbeddingBackend):
    """OpenAI Embedding API 后端

    需要设置 OPENAI_API_KEY 环境变量。
    可通过 MINDFORGE_EMBEDDING_MODEL 指定模型（默认 text-embedding-3-small）。
    """

    def __init__(self, model_name: str = "text-embedding-3-small",
                 api_key: str = "", api_base: str = ""):
        self._model_name = model_name
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._api_base = api_base or os.environ.get("OPENAI_API_BASE",
                                                     "https://api.openai.com/v1")
        self._dimension = 1536  # text-embedding-3-small 默认 1536 维
        self._available = False
        self._load_attempted = False

    @property
    def is_available(self) -> bool:
        if not self._load_attempted:
            self._try_load()
        return self._available

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def _try_load(self):
        self._load_attempted = True
        if not self._api_key:
            logger.warning("OpenAIBackend: OPENAI_API_KEY 未设置")
            self._available = False
            return
        try:
            pass
            # 探测维度：发送一条测试请求
            result = self._call_api(["test"])
            if result and len(result) > 0:
                self._dimension = len(result[0])
                self._available = True
                logger.info("OpenAIBackend 加载成功: %s (dim=%d)",
                            self._model_name, self._dimension)
            else:
                self._available = False
        except Exception as e:
            logger.warning("OpenAIBackend 加载失败: %s", e)
            self._available = False

    def _call_api(self, texts: List[str]) -> Optional[List[List[float]]]:
        """调用 OpenAI Embeddings API"""
        if not self._api_key:
            return None
        try:
            import urllib.request
            import urllib.error

            url = f"{self._api_base}/embeddings"
            payload = json.dumps({
                "model": self._model_name,
                "input": texts,
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            embeddings = [item["embedding"] for item in data["data"]]
            return embeddings
        except urllib.error.HTTPError as e:
            logger.warning("OpenAI API HTTP 错误: %d %s", e.code, e.reason)
            return None
        except Exception as e:
            logger.warning("OpenAI API 调用失败: %s", e)
            return None

    def encode(self, text: str) -> Optional[List[float]]:
        if not self.is_available or not text:
            return None
        result = self._call_api([text])
        if result and len(result) > 0:
            return result[0]
        return None

    def encode_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        if not self.is_available or not texts:
            return None
        # OpenAI API 单次最多 2048 条
        all_results = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            result = self._call_api(batch)
            if result:
                all_results.extend(result)
            else:
                return None
        return all_results


# ===== Ollama 后端 =====

class OllamaBackend(EmbeddingBackend):
    """Ollama 本地嵌入后端

    需要本地运行 Ollama 服务。通过 OLLAMA_HOST 配置地址（默认 http://localhost:11434）。
    通过 MINDFORGE_EMBEDDING_MODEL 指定模型（默认 nomic-embed-text）。
    """

    def __init__(self, model_name: str = "nomic-embed-text",
                 host: str = ""):
        self._model_name = model_name
        self._host = host or os.environ.get("OLLAMA_HOST",
                                            "http://localhost:11434")
        self._dimension = 768  # nomic-embed-text 默认 768 维
        self._available = False
        self._load_attempted = False

    @property
    def is_available(self) -> bool:
        if not self._load_attempted:
            self._try_load()
        return self._available

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def _try_load(self):
        self._load_attempted = True
        try:
            result = self._call_api("test")
            if result:
                self._dimension = len(result)
                self._available = True
                logger.info("OllamaBackend 加载成功: %s (dim=%d)",
                            self._model_name, self._dimension)
            else:
                self._available = False
        except Exception as e:
            logger.warning("OllamaBackend 加载失败: %s", e)
            self._available = False

    def _call_api(self, text: str) -> Optional[List[float]]:
        """调用 Ollama Embeddings API"""
        try:
            import urllib.request
            import urllib.error

            url = f"{self._host}/api/embeddings"
            payload = json.dumps({
                "model": self._model_name,
                "prompt": text,
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("embedding")
        except urllib.error.URLError as e:
            logger.warning("Ollama 连接失败（服务未启动？）: %s", e)
            return None
        except Exception as e:
            logger.warning("Ollama API 调用失败: %s", e)
            return None

    def encode(self, text: str) -> Optional[List[float]]:
        if not self.is_available or not text:
            return None
        return self._call_api(text)

    def encode_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """批量编码文本为向量。

        v5.4.8 P2-001 修复：
        - 小批量（<4）使用串行，避免线程池开销
        - 部分失败时返回部分结果而不是整体失败
        """
        if not self.is_available or not texts:
            return None

        n = len(texts)

        # 小批量：串行处理，避免线程池开销
        if n < 4:
            results = []
            for text in texts:
                vec = self._call_api(text)
                if vec is not None:
                    results.append(vec)
            return results if results else None

        # 大批量：并发处理
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = [None] * n
        failed_count = 0

        with ThreadPoolExecutor(max_workers=min(8, n)) as executor:
            future_to_idx = {
                executor.submit(self._call_api, text): i
                for i, text in enumerate(texts)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    vec = future.result()
                    if vec is not None:
                        results[idx] = vec
                    else:
                        failed_count += 1
                except Exception:
                    failed_count += 1

        # 全部失败
        if failed_count == n:
            return None

        # 部分失败：记录警告但返回成功部分
        if failed_count > 0:
            logger.warning(
                "Ollama batch: %d/%d succeeded, %d failed",
                n - failed_count, n, failed_count
            )

        # 过滤掉 None
        return [r for r in results if r is not None]


# ===== 自定义 HTTP 后端 =====

class HTTPBackend(EmbeddingBackend):
    """自定义 HTTP 嵌入后端

    通过 MINDFORGE_EMBEDDING_API_URL 指定端点 URL。
    端点需接受 POST {"texts": ["..."]} 并返回 {"embeddings": [[...]]}。
    可选设置 MINDFORGE_EMBEDDING_API_KEY 用于 Bearer 认证。
    """

    def __init__(self, api_url: str = "", api_key: str = "",
                 model_name: str = "custom", dimension: int = 0):
        self._model_name = model_name
        self._api_url = api_url or os.environ.get("MINDFORGE_EMBEDDING_API_URL", "")
        self._api_key = api_key or os.environ.get("MINDFORGE_EMBEDDING_API_KEY", "")
        self._dimension = dimension or int(os.environ.get("MINDFORGE_EMBEDDING_DIM", "0"))
        self._available = False
        self._load_attempted = False

    @property
    def is_available(self) -> bool:
        if not self._load_attempted:
            self._try_load()
        return self._available

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def _try_load(self):
        self._load_attempted = True
        if not self._api_url:
            logger.warning("HTTPBackend: MINDFORGE_EMBEDDING_API_URL 未设置")
            self._available = False
            return
        try:
            result = self._call_api(["test"])
            if result and len(result) > 0:
                if self._dimension == 0:
                    self._dimension = len(result[0])
                self._available = True
                logger.info("HTTPBackend 加载成功: %s (dim=%d)",
                            self._api_url, self._dimension)
            else:
                self._available = False
        except Exception as e:
            logger.warning("HTTPBackend 加载失败: %s", e)
            self._available = False

    def _call_api(self, texts: List[str]) -> Optional[List[List[float]]]:
        try:
            import urllib.request
            import urllib.error

            payload = json.dumps({"texts": texts}).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            req = urllib.request.Request(
                self._api_url,
                data=payload,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                return data
            return data.get("embeddings") or data.get("data")
        except Exception as e:
            logger.warning("HTTP Backend 调用失败: %s", e)
            return None

    def encode(self, text: str) -> Optional[List[float]]:
        if not self.is_available or not text:
            return None
        result = self._call_api([text])
        if result and len(result) > 0:
            return result[0]
        return None

    def encode_batch(self, texts: List[str], batch_size: int = 100) -> Optional[List[List[float]]]:
        """批量编码文本为向量。

        v5.4.8 P1-002 修复：添加 batch_size 限制，分批发送请求，
        防止大量文本导致 413 错误或超时。
        """
        if not self.is_available or not texts:
            return None

        # 分批处理
        all_results = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            result = self._call_api(chunk)
            if result is None:
                logger.warning("HTTPBackend batch %d-%d failed", i, i + len(chunk))
                return None
            all_results.extend(result)
        return all_results


# ===== 后端工厂 =====

_BACKEND_REGISTRY = {
    "sentence_transformers": SentenceTransformerBackend,
    "openai": OpenAIBackend,
    "ollama": OllamaBackend,
    "http": HTTPBackend,
}


def create_backend(backend_name: str = "", model_name: str = "",
                   **kwargs) -> EmbeddingBackend:
    """创建嵌入后端实例

    Args:
        backend_name: 后端名称，空则从环境变量 MINDFORGE_EMBEDDING_BACKEND 读取，
                      默认 sentence_transformers
        model_name: 模型名称，空则从环境变量读取
        **kwargs: 传递给后端构造函数的额外参数

    Returns:
        EmbeddingBackend 实例
    """
    backend_name = backend_name or os.environ.get(
        "MINDFORGE_EMBEDDING_BACKEND", "sentence_transformers")
    model_name = model_name or os.environ.get("MINDFORGE_EMBEDDING_MODEL", "")

    cls = _BACKEND_REGISTRY.get(backend_name)
    if cls is None:
        raise ValueError(
            f"未知嵌入后端: {backend_name!r}。"
            f"支持: {', '.join(sorted(_BACKEND_REGISTRY))}"
        )

    if backend_name == "openai":
        model = model_name or "text-embedding-3-small"
        return cls(model_name=model, **kwargs)
    elif backend_name == "ollama":
        model = model_name or "nomic-embed-text"
        return cls(model_name=model, **kwargs)
    elif backend_name == "http":
        return cls(model_name=model_name or "custom", **kwargs)
    else:
        model = model_name or DEFAULT_MODEL
        return cls(model_name=model, **kwargs)


# ===== 嵌入引擎（统一接口，兼容旧 API） =====

class EmbeddingEngine:
    """语义嵌入引擎（多后端适配器，v5.4.6 增强）

    封装多种嵌入后端，提供：
    - 文本 → 向量编码（encode / encode_batch）
    - 向量序列化（serialize / deserialize，用于 SQLite BLOB 存储）
    - 余弦相似度计算
    - 懒加载：首次 encode 时才加载模型/连接后端
    - 多后端支持：sentence-transformers / OpenAI / Ollama / 自定义 HTTP

    后端选择优先级：
    1. 构造参数 backend=
    2. 环境变量 MINDFORGE_EMBEDDING_BACKEND
    3. 默认 sentence_transformers

    使用方式：
        engine = EmbeddingEngine()  # 自动选择后端
        if engine.is_available:
            vec = engine.encode("hello world")

        # 指定后端
        engine = EmbeddingEngine(backend="openai")
        engine = EmbeddingEngine(backend="ollama", model_name="nomic-embed-text")
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self, model_name: str = "", backend: str = ""):
        if hasattr(self, "_initialized"):
            # v5.5.3 fix: 检查参数是否不同，如不同则记录警告
            requested_backend = backend or os.environ.get("MINDFORGE_EMBEDDING_BACKEND", "sentence_transformers")
            requested_model = model_name or os.environ.get("MINDFORGE_EMBEDDING_MODEL", "")
            if requested_backend != self._backend_name or (requested_model and requested_model != self._model_name):
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    "EmbeddingEngine is a singleton: requested backend=%s model=%s, "
                    "but using existing instance backend=%s model=%s",
                    requested_backend, requested_model, self._backend_name, self._model_name
                )
            return
        try:
            self._initialized = True
            self._backend_name = backend or os.environ.get(
                "MINDFORGE_EMBEDDING_BACKEND", "sentence_transformers")
            self._backend: Optional[EmbeddingBackend] = None
            self._model_name = model_name or os.environ.get(
                "MINDFORGE_EMBEDDING_MODEL", "")
            if not self._model_name:
                if self._backend_name == "openai":
                    self._model_name = "text-embedding-3-small"
                elif self._backend_name == "ollama":
                    self._model_name = "nomic-embed-text"
                else:
                    self._model_name = DEFAULT_MODEL
            self._dimension = DEFAULT_DIMENSION
            self._load_attempted = False
            self._available = False
        except Exception:
            # P1 修复：初始化失败时重置单例，允许后续调用重试
            # （此前失败后 _instance 已创建但半初始化，后续调用直接拿到坏实例）
            with EmbeddingEngine._lock:
                EmbeddingEngine._instance = None
            raise

    @property
    def is_available(self) -> bool:
        """嵌入后端是否可用"""
        if not self._load_attempted:
            self._try_load()
        return self._available

    @property
    def dimension(self) -> int:
        """嵌入向量维度"""
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def backend_name(self) -> str:
        """当前后端名称"""
        return self._backend_name

    def _try_load(self):
        """懒加载嵌入后端"""
        self._load_attempted = True
        try:
            self._backend = create_backend(
                backend_name=self._backend_name,
                model_name=self._model_name,
            )
            if self._backend.is_available:
                self._dimension = self._backend.dimension
                self._available = True
                logger.info("EmbeddingEngine 加载成功 [%s]: %s (dim=%d)",
                            self._backend_name, self._model_name, self._dimension)
            else:
                self._available = False
        except Exception as e:
            logger.warning("EmbeddingEngine 加载失败: %s", e)
            self._available = False

    def encode(self, text: str) -> Optional[List[float]]:
        """编码单条文本为向量

        Args:
            text: 输入文本

        Returns:
            嵌入向量 (List[float])，或 None（模型不可用时）
        """
        if not self.is_available or not text:
            return None
        return self._backend.encode(text)

    def encode_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """批量编码文本为向量

        Args:
            texts: 输入文本列表

        Returns:
            嵌入向量列表，或 None（模型不可用时）
        """
        if not self.is_available or not texts:
            return None
        return self._backend.encode_batch(texts)

    @staticmethod
    def serialize(vec: List[float]) -> bytes:
        """将向量序列化为 bytes（用于 SQLite BLOB 存储）

        格式: 4 字节 float32 数组（小端序）
        """
        return struct.pack(f'<{len(vec)}f', *vec)

    @staticmethod
    def deserialize(blob: bytes) -> List[float]:
        """从 bytes 反序列化向量"""
        if not blob:
            return []
        count = len(blob) // 4
        return list(struct.unpack(f'<{count}f', blob))

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        """计算两个向量的余弦相似度

        v5.4.6 修复：不再假设向量已归一化，改为完整余弦相似度计算。
        降级模式或外部 API 返回的向量可能未归一化，
        之前直接点积导致相同向量返回非 1.0 的错误结果。

        Args:
            v1: 向量 1
            v2: 向量 2

        Returns:
            余弦相似度 [-1.0, 1.0]
        """
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = sum(a * a for a in v1) ** 0.5
        norm2 = sum(b * b for b in v2) ** 0.5
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return max(-1.0, min(1.0, dot / (norm1 * norm2)))

    @staticmethod
    def cosine_similarity_batch(
        query_vec: List[float],
        candidates: List[Tuple[str, List[float]]],
        top_k: int = 20
    ) -> List[Tuple[str, float]]:
        """批量计算余弦相似度并返回 top_k 结果

        Args:
            query_vec: 查询向量
            candidates: [(memory_id, embedding_vec), ...]
            top_k: 返回前 k 个结果

        Returns:
            [(memory_id, score), ...] 按分数降序
        """
        if not query_vec or not candidates:
            return []
        scored = []
        for mem_id, vec in candidates:
            if not vec or len(vec) != len(query_vec):
                continue
            score = EmbeddingEngine.cosine_similarity(query_vec, vec)
            scored.append((mem_id, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
