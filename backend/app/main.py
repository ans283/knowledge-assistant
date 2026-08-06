from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ingest as ingest_api
from app.api import query as query_api
from app.config import settings
from app.core.store import get_collection


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the BM25 index from whatever is already in Chroma.

    The sparse index lives in memory and is rebuilt at startup rather than
    persisted — see BM25Index for the corpus-size assumption behind that.
    """
    from app.core.retriever import ensure_bm25
    ensure_bm25()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],   # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_api.router)
app.include_router(query_api.router)


@app.get("/health")
def health():
    from app.core.retriever import get_bm25_index

    col = get_collection()
    return {
        "status": "ok",
        "collection": col.name,
        "chunks_indexed": col.count(),
        "bm25_indexed": get_bm25_index().size,
        "llm_provider": settings.llm_provider,
    }