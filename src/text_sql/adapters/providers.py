from __future__ import annotations

from langchain_openai import ChatOpenAI


class LangChainOpenAIChatClient:
    def __init__(self, model: str, temperature: float) -> None:
        self._llm = ChatOpenAI(model=model, temperature=temperature)

    def complete_tracked(self, user_text: str) -> tuple[str, dict[str, int]]:
        """Return ``(content, usage)`` where *usage* has prompt/completion/total_tokens.

        Tries ``usage_metadata`` first (LangChain ≥ 0.2), then falls back to
        ``response_metadata["token_usage"]`` from the OpenAI adapter.
        Returns an empty dict when neither field is available.
        """
        response = self._llm.invoke(user_text)
        content = str(getattr(response, "content", response))

        usage: dict[str, int] = {}
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            usage = {
                "prompt_tokens": int(getattr(meta, "input_tokens", 0)),
                "completion_tokens": int(getattr(meta, "output_tokens", 0)),
                "total_tokens": int(getattr(meta, "total_tokens", 0)),
            }
        else:
            token_usage = (getattr(response, "response_metadata", None) or {}).get(
                "token_usage", {}
            )
            if token_usage:
                usage = {
                    "prompt_tokens": int(token_usage.get("prompt_tokens", 0)),
                    "completion_tokens": int(token_usage.get("completion_tokens", 0)),
                    "total_tokens": int(token_usage.get("total_tokens", 0)),
                }

        return content, usage
