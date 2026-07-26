import json
import chromadb
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

COSINE = {"hnsw": {"space": "cosine"}}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_chroma_roundtrip():
    """Vectors go in, correct neighbour comes back, metadata survives intact."""
    col = chromadb.EphemeralClient().get_or_create_collection(
        name="probe_roundtrip", configuration=COSINE
    )
    col.add(
        ids=["c1", "c2", "c3"],
        documents=["parental leave policy", "expense reimbursement", "password rotation"],
        embeddings=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]],
        metadatas=[
            {"doc_id": "handbook", "char_start": 100, "char_end": 250},
            {"doc_id": "handbook", "char_start": 400, "char_end": 520},
            {"doc_id": "secpol", "char_start": 10, "char_end": 180},
        ],
    )

    res = col.query(
        query_embeddings=[[0.9, 0.1, 0.0, 0.0]],
        n_results=2,
        include=["documents", "metadatas", "distances"],
    )

    assert res["ids"][0][0] == "c1"
    assert res["metadatas"][0][0]["char_start"] == 100
    assert res["distances"][0][0] < res["distances"][0][1]
    assert col.count() == 3


def test_cosine_space_is_active():
    """An identical vector must return ~0 distance under cosine.
    Under default L2 this would also be ~0, so we check the orthogonal
    case too: cosine distance of a perpendicular vector is ~1."""
    col = chromadb.EphemeralClient().get_or_create_collection(
        name="probe_cosine", configuration=COSINE
    )
    col.add(ids=["x", "y"], embeddings=[[1.0, 0.0], [0.0, 1.0]])

    res = col.query(query_embeddings=[[1.0, 0.0]], n_results=2, include=["distances"])
    d_same, d_orth = res["distances"][0]

    assert d_same == pytest.approx(0.0, abs=1e-5)
    assert d_orth == pytest.approx(1.0, abs=1e-5)


def test_structured_metadata_survives_json_roundtrip():
    """Chunk metadata must be flat scalars for reliable `where` filtering, so
    anything structured is JSON-encoded on write and decoded on read. This
    pins the convention used by the chunk schema in Step 4."""
    col = chromadb.EphemeralClient().get_or_create_collection(
        name="probe_metadata", configuration=COSINE
    )

    pages = [11, 12, 13]
    col.add(
        ids=["chunk_1"],
        documents=["parental leave is granted for..."],
        embeddings=[[1.0, 0.0]],
        metadatas=[{
            "doc_id": "handbook",
            "section_path": "Leave Policy > Parental Leave",
            "char_start": 18422,
            "char_end": 19180,
            "pages_json": json.dumps(pages),   # structured → string
        }],
    )

    meta = col.get(ids=["chunk_1"], include=["metadatas"])["metadatas"][0]

    assert meta["char_start"] == 18422
    assert isinstance(meta["char_start"], int)          # ints stay ints
    assert json.loads(meta["pages_json"]) == pages      # decodes cleanly
    assert meta["section_path"] == "Leave Policy > Parental Leave"


def test_metadata_filter_works_on_scalars():
    """`where` filtering on doc_id is what /query uses to scope retrieval."""
    col = chromadb.EphemeralClient().get_or_create_collection(
        name="probe_filter", configuration=COSINE
    )
    col.add(
        ids=["a1", "b1"],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        metadatas=[{"doc_id": "handbook"}, {"doc_id": "secpol"}],
    )

    res = col.query(
        query_embeddings=[[1.0, 0.0]],
        n_results=5,
        where={"doc_id": "secpol"},
        include=["metadatas"],
    )

    assert res["ids"][0] == ["b1"]