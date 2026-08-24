ANSWER_SYSTEM = """You are a repository AI assistant. Answer questions using ONLY the provided source context.
Do not invent file paths, APIs, function names, or behavior that is not in the snippets.
Always cite sources using the format path:start-end (example: src/auth/service.py:10-42).
For git-history context you may also cite commit:sha (example: commit:abc1234).
If the context is insufficient, clearly say what is missing and that it was not found in the indexed sources.
Be precise and technical. Every factual claim must be backed by a citation."""

SOURCE_CHECK_SYSTEM = """Rewrite the answer so every factual claim is entailed by the provided snippets
and backed by a citation in the form path:start-end or commit:sha from the allowed sources list.
Strip or remove any claim that is not supported by the snippets.
Do not invent file paths or APIs. If nothing is grounded, say it was not found in the indexed sources.
Keep the answer concise. Return the corrected answer only."""


def build_context_block(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c['file']}:{c['start_line']}-{c['end_line']}\n```\n{c['content']}\n```"
        )
    return "\n\n".join(parts)


def build_answer_messages(
    question: str, chunks: list[dict], history: str = ""
) -> list[dict]:
    context = build_context_block(chunks)
    hist = f"\nConversation so far:\n{history}\n" if history.strip() else ""
    return [
        {"role": "system", "content": ANSWER_SYSTEM},
        {
            "role": "user",
            "content": (
                f"{hist}Question: {question}\n\nSource context:\n{context}\n\n"
                "Provide an answer with citations, using only the source context."
            ),
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
