ANSWER_SYSTEM = """You are a repository AI assistant. Answer questions using ONLY the provided source context.
Always cite sources using the format path:start-end (example: src/auth/service.py:10-42).
If the context is insufficient, say what is missing. Be precise and technical."""


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
