ANSWER_SYSTEM = """You are a repository AI assistant. Answer questions using ONLY the provided source context.
Always cite sources using the format path:start-end (example: src/auth/service.py:10-42).
For git-history context you may also cite commit:sha (example: commit:abc1234).
If the context is insufficient, say what is missing. Be precise and technical."""

SOURCE_CHECK_SYSTEM = """Rewrite the answer so every factual claim is backed by a citation in the form path:start-end
or commit:sha drawn from the provided sources list. Do not invent file paths. Keep the answer concise."""


def build_context_block(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c['file']}:{c['start_line']}-{c['end_line']}\n```\n{c['content']}\n```"
        )
    return "\n\n".join(parts)


def build_answer_messages(question: str, chunks: list[dict]) -> list[dict]:
    context = build_context_block(chunks)
    return [
        {"role": "system", "content": ANSWER_SYSTEM},
        {
            "role": "user",
            "content": f"Question: {question}\n\nSource context:\n{context}\n\nProvide an answer with citations.",
        },
    ]


def build_source_check_messages(question: str, answer: str, chunks: list[dict]) -> list[dict]:
    sources = "\n".join(
        f"- {c['file']}:{c.get('start_line', 1)}-{c.get('end_line', 1)}" for c in chunks
    )
    return [
        {"role": "system", "content": SOURCE_CHECK_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\nDraft answer:\n{answer}\n\n"
                f"Allowed sources:\n{sources}\n\nReturn the corrected answer only."
            ),
        },
    ]
