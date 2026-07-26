import chromadb
from chromadb.api.models.Collection import Collection

from app.config import settings

_client: chromadb.ClientAPI | None = None


def get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    return _client


def get_collection(name: str | None = None) -> Collection:
    """Chunk collection. Embeddings are ALWAYS supplied explicitly by us —
    never let Chroma's default embedding function run, or you end up with
    two different vector spaces in one collection."""
    return get_client().get_or_create_collection(
        name=name or settings.collection_name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=None,
    )