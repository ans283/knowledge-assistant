import chromadb

client = chromadb.EphemeralClient()
col = client.get_or_create_collection(
    name="probe", configuration={"hnsw": {"space": "cosine"}}
)

col.add(
    ids=["a", "b"],
    documents=["parental leave", "password rotation"],
    embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    metadatas=[{"doc_id": "h", "char_start": 100}, {"doc_id": "s", "char_start": 5}],
)

res = col.query(
    query_embeddings=[[1.0, 0.0, 0.0]],
    n_results=2,
    include=["documents", "metadatas", "distances"],
)

print("chroma:", chromadb.__version__)
print("ids:", res["ids"][0])
print("distances:", res["distances"][0])
print("meta:", res["metadatas"][0][0])
print("count:", col.count())