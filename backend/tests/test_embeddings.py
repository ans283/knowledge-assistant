import pytest

from app.config import settings
from app.core.embeddings import (
    QUERY_INSTRUCTION, cosine_similarity, dimension, embed_documents, embed_query,
)

pytestmark = pytest.mark.slow      # loads a ~400MB model


def test_dimension_matches_config():
    """A mismatch here would corrupt the collection silently — vectors of the
    wrong width either error at insert or compare meaninglessly."""
    assert dimension() == settings.embedding_dim


def test_vectors_are_unit_length():
    """Required for cosine distances to fall on the 0..1 scale the abstention
    threshold assumes."""
    for vector in embed_documents(["personal data retention policy"]):
        assert cosine_similarity(vector, vector) == pytest.approx(1.0, abs=1e-4)


def test_embedding_is_deterministic():
    """Reproducible eval numbers depend on this."""
    a = embed_documents(["Personal data must be retained for seven years."])[0]
    b = embed_documents(["Personal data must be retained for seven years."])[0]
    assert cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-6)


def test_batch_and_single_agree():
    solo = embed_documents(["data retention"])[0]
    batched = embed_documents(["unrelated text", "data retention", "more filler"])[1]
    assert cosine_similarity(solo, batched) == pytest.approx(1.0, abs=1e-5)


def test_empty_input_returns_empty():
    assert embed_documents([]) == []


def test_semantic_similarity_beats_lexical_overlap():
    """The whole reason for dense retrieval: 'how long do we keep personal
    data' shares almost no words with the passage that answers it."""
    passage = embed_documents([
        "Personal data shall be retained for a period of seven years following "
        "the termination of the customer relationship, after which it is securely destroyed."
    ])[0]
    unrelated = embed_documents([
        "All source code changes must be reviewed by a second engineer before merge."
    ])[0]

    question = embed_query("how long do we keep customer information")

    assert cosine_similarity(question, passage) > cosine_similarity(question, unrelated)


def test_query_prefix_changes_the_vector():
    """bge models are trained with an asymmetric prefix. If prefixed and bare
    vectors were identical, embed_query would be doing nothing."""
    bare = embed_documents(["how long do we keep customer data"])[0]
    prefixed = embed_query("how long do we keep customer data")
    assert cosine_similarity(bare, prefixed) < 0.999


def test_section_path_prefix_separates_boilerplate():
    """The Step 4 disambiguation, verified in vector space rather than by
    string comparison. Five SANS policies share near-identical Purpose text."""
    shared = (
        "Our policy aims to establish a comprehensive framework for protecting "
        "organizational assets and ensuring regulatory compliance."
    )
    bare_a, bare_b = embed_documents([shared, shared])
    prefixed_a, prefixed_b = embed_documents([
        f"Privacy Management Policy > Purpose\n\n{shared}",
        f"Software Development Management Policy > Purpose\n\n{shared}",
    ])

    assert cosine_similarity(bare_a, bare_b) == pytest.approx(1.0, abs=1e-6)
    assert cosine_similarity(prefixed_a, prefixed_b) < 0.99


def test_privacy_query_prefers_privacy_document():
    """End-to-end sanity: does the section prefix actually route a query to the
    right one of two otherwise-identical boilerplate chunks?"""
    shared = (
        "Our policy aims to establish a comprehensive framework and provide "
        "clear guidelines and procedures across the organization."
    )
    privacy, software = embed_documents([
        f"Privacy Management Policy > Purpose\n\n{shared}",
        f"Software Development Management Policy > Purpose\n\n{shared}",
    ])

    question = embed_query("what is the purpose of the privacy policy")

    assert cosine_similarity(question, privacy) > cosine_similarity(question, software)