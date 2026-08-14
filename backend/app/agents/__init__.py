"""Agent package. Import chat/run_agent from app.agents.workflow when needed."""

__all__ = ["chat", "run_agent"]


def __getattr__(name: str):
    if name in {"chat", "run_agent"}:
        from app.agents.workflow import chat, run_agent

        return {"chat": chat, "run_agent": run_agent}[name]
    raise AttributeError(name)
