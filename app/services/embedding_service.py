from __future__ import annotations

import hashlib
import math
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path


DEFAULT_EMBEDDING_DIM = 256
DEFAULT_REAL_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_REAL_EMBEDDER_NAME = "bge-small-zh-v1.5"
DEFAULT_MODEL_CACHE_DIR = Path(r"D:\LEARNING\Tools\model_cache\huggingface")
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


class BaseEmbedder(ABC):
    dimension: int
    name: str

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        raise NotImplementedError


class HashTextEmbedder(BaseEmbedder):
    """Deterministic local embedder for Phase 5A.

    This is a lightweight hashing embedder. It does not load external models,
    download files, or call APIs. It is only a replaceable interface baseline.
    """

    name = "hash-text-v1"

    def __init__(self, dimension: int = DEFAULT_EMBEDDING_DIM) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive.")
        self.dimension = dimension

    def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in _tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            weight = 1.0 + (len(token) / 16.0)
            for offset in range(0, 16, 4):
                index = int.from_bytes(digest[offset : offset + 4], "big") % self.dimension
                sign = 1.0 if digest[16 + (offset // 4)] % 2 == 0 else -1.0
                vector[index] += sign * weight
        return _normalize(vector)


class RealEmbeddingModelEmbedder(BaseEmbedder):
    """Local sentence-transformers embedder for Phase 5B.

    This class loads a local sentence-transformers model and stores model
    cache under a user-controlled D: drive directory by default. It does not
    call remote inference APIs.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_REAL_EMBEDDING_MODEL,
        cache_folder: str | Path = DEFAULT_MODEL_CACHE_DIR,
    ) -> None:
        self.model_name = model_name
        self.cache_folder = Path(cache_folder)
        self.cache_folder.mkdir(parents=True, exist_ok=True)
        _configure_model_cache_environment(self.cache_folder)

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Install dependencies with the NOTEBOOK_AI conda Python first."
            ) from exc

        try:
            self._model = SentenceTransformer(model_name, cache_folder=str(self.cache_folder))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load embedding model {model_name!r}. "
                f"Check that it is available in cache or can be downloaded to {self.cache_folder}."
            ) from exc

        self.name = _model_alias(model_name)
        if hasattr(self._model, "get_embedding_dimension"):
            self.dimension = int(self._model.get_embedding_dimension())
        else:
            self.dimension = int(self._model.get_sentence_embedding_dimension())

    def embed_text(self, text: str) -> list[float]:
        embedding = self._model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [float(value) for value in embedding.tolist()]


def create_embedder(
    embedder_name: str = "hash-text-v1",
    cache_folder: str | Path | None = None,
) -> BaseEmbedder:
    if embedder_name == "hash-text-v1":
        return HashTextEmbedder()
    if embedder_name in {"bge-small-zh-v1.5", DEFAULT_REAL_EMBEDDING_MODEL}:
        return RealEmbeddingModelEmbedder(
            model_name=DEFAULT_REAL_EMBEDDING_MODEL,
            cache_folder=cache_folder or DEFAULT_MODEL_CACHE_DIR,
        )
    raise ValueError(f"Unsupported embedder: {embedder_name}")


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Vectors must have the same dimension.")
    return sum(a * b for a, b in zip(left, right))


def _tokenize(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in TOKEN_RE.finditer(text)]
    cjk_tokens = [token for token in tokens if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    cjk_bigrams = [left + right for left, right in zip(cjk_tokens, cjk_tokens[1:])]
    return tokens + cjk_bigrams


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def _configure_model_cache_environment(cache_folder: Path) -> None:
    hub_cache = cache_folder / "hub"
    torch_cache = cache_folder.parent / "torch"
    hub_cache.mkdir(parents=True, exist_ok=True)
    torch_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_folder))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hub_cache))
    os.environ.setdefault("TORCH_HOME", str(torch_cache))


def _model_alias(model_name: str) -> str:
    if model_name == DEFAULT_REAL_EMBEDDING_MODEL:
        return DEFAULT_REAL_EMBEDDER_NAME
    return model_name
