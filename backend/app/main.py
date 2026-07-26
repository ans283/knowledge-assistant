from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.store import get_collection

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],   # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    col = get_collection()
    return {
        "status": "ok",
        "collection": col.name,
        "chunks_indexed": col.count(),
    }