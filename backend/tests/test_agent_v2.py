from app.agents.query_utils import citations_match, classify_query, ensure_citations_footer


def test_classify_history_questions():
    kinds = classify_query("Which module has the highest churn?")
    assert "HISTORY" in kinds
    assert "RAG" in kinds


def test_classify_docs_questions():
    kinds = classify_query("How do I install this? Check the README")
    assert "DOCS" in kinds


def test_classify_graph_and_code():
    assert "GRAPH" in classify_query("Who depends on PaymentService?")
    assert "CODE" in classify_query("Where is createOrder defined?")


def test_citations_match_path():
    chunks = [
        {
            "file": "src/auth/service.py",
            "start_line": 10,
            "end_line": 42,
            "content": "def login(): ...",
        }
    ]
    answer = "Auth is in src/auth/service.py:10-42"
    assert citations_match(answer, chunks) is True


def test_citations_match_fails_without_citation():
    chunks = [
        {
            "file": "src/auth/service.py",
            "start_line": 10,
            "end_line": 42,
            "content": "def login(): ...",
        }
    ]
    answer = "Authentication is somewhere in the auth module."
    assert citations_match(answer, chunks) is False


def test_ensure_citations_footer():
    chunks = [
        {
            "file": "src/auth/service.py",
            "start_line": 10,
            "end_line": 20,
            "content": "code",
        }
    ]
    out = ensure_citations_footer("Auth lives in the service layer.", chunks)
    assert "Sources:" in out
    assert "src/auth/service.py:10-20" in out
