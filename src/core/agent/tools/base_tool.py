from __future__ import annotations

from typing import Any, Optional, Type

from pydantic import BaseModel


class BaseTool:
    """
    Minimal stand-in for LangChain's BaseTool.

    This project previously used `langchain.tools.BaseTool` mainly as a convention:
    - `name` / `description` metadata
    - optional `args_schema` (Pydantic model)
    - `_run` / `_arun` execution hooks

    We keep the same surface area so the rest of the code can run without LangChain.
    """

    name: str = ""
    description: str = ""
    args_schema: Optional[Type[BaseModel]] = None

    def __init__(self, **_: Any) -> None:
        # Accept and ignore arbitrary kwargs to match LangChain construction patterns.
        pass

    def run(self, *args: Any, **kwargs: Any) -> Any:
        return self._run(*args, **kwargs)

    async def arun(self, *args: Any, **kwargs: Any) -> Any:
        return await self._arun(*args, **kwargs)

    def _run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("Tool must implement _run().")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("Tool must implement _arun().")

