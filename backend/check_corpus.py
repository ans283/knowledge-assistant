"""Throwaway: verify loader output without long lines that terminals wrap."""
from app.config import settings
from app.core.loaders import discover_corpus, load_document

PROBES = [
    "consistent approach",
    "collection, storage, use",
    "disposition, and GitLab",
    "Self-Managed and Dedicated",
    "Validation Management Policy",
]

for path in discover_corpus(settings.corpus_dir):
    d = load_document(path)
    words = d.text.split()
    glued = [w for w in words if len(w) > 24]
    print(d.doc_id[:38])
    print("   page marker left:", "Page 1" in d.text)
    print("   long tokens:", len(glued), glued[:2])
    print("   'Purpose' own line:", "Purpose" in d.text.split("\n"))

print()
print("--- probes (True = space intact in the data) ---")
joined = "\n".join(load_document(p).text for p in discover_corpus(settings.corpus_dir))
for probe in PROBES:
    print(f"   {probe!r}:", probe in joined)