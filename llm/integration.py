"""LLM providers — LiteLLM cloud gateway first, offline fallback second.

Supported LLM_PROVIDER values:
  litellm -> DB-driven cloud gateway (Gemini / Groq / OpenRouter :free /
            GitHub Models / OpenAI / custom). Chat resolves ordered providers
            from MongoDB in app.services; without DB this builds a single
            env-based endpoint (tests / boot probe).
  openai   -> legacy single-provider via OPENAI_API_KEY.
  keyword  -> zero-dependency lexical tool router (offline dev / fallback).
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class LLMProvider(ABC):
    """Base class for all LLM providers."""

    @abstractmethod
    async def generate(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float] = None,
        stop_sequences: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate response and determine if node should be called."""
        pass


class OpenAIProvider(LLMProvider):
    """OpenAI GPT integration with function calling."""

    def __init__(self, api_key: str = None):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "LLM_PROVIDER=openai requires the 'openai' package. "
                "Install base requirements: pip install -r requirements.txt"
            ) from exc

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set")

        self.client = AsyncOpenAI(api_key=self.api_key)

    async def generate(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float] = None,
        stop_sequences: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate response using OpenAI with function calling."""

        # Build messages
        messages = [
            {
                "role": "system",
                "content": project["llm_prompt"],
            }
        ]

        # Add conversation history
        for msg in conversation_history[-5:]:
            messages.append(
                {
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                }
            )

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Build tools/functions from nodes
        tools = []
        for node in nodes:
            # Convert node schema to OpenAI function format
            properties = {}
            required = []

            for input_name, input_type in node["node_schema"]["inputs"].items():
                properties[input_name] = {
                    "type": "string" if input_type == "string" else "number",
                    "description": f"{input_name} parameter",
                }
                required.append(input_name)

            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": node["name"],
                        "description": node["description"],
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )

        # Call OpenAI
        try:
            response = await self.client.chat.completions.create(
                model=project["llm_config"].get("model_name", "gpt-4o-mini"),
                messages=messages,
                tools=tools if tools else None,
                temperature=project["llm_config"].get("temperature", 0.7),
                max_tokens=project["llm_config"].get("max_tokens", 2048),
            )

            message = response.choices[0].message

            # Check if function/tool was called
            if message.tool_calls:
                tool_call = message.tool_calls[0]
                return {
                    "action": "call_node",
                    "node_name": tool_call.function.name,
                    "inputs": json.loads(tool_call.function.arguments),
                    "text_response": message.content
                    or f"Let me help you with {tool_call.function.name}...",
                }

            # Regular chat response
            return {"action": "chat", "text_response": message.content}

        except Exception as e:
            return {
                "action": "chat",
                "text_response": f"I apologize, but I encountered an error: {str(e)}",
            }


class KeywordMatchProvider(LLMProvider):
    """
    Domain-agnostic fallback when no real LLM is available.

    Does NOT hardcode product intents. Routes only from each project's
    node name / description / input schema via app.core.tool_router.
    """

    async def generate(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float] = None,
        stop_sequences: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        # Second-pass / summarize mode (tools disabled)
        if not nodes:
            for msg in reversed(conversation_history or []):
                content = str(msg.get("content") or "")
                if content.startswith("TOOL RESULT") or "TOOL RESULT for" in content:
                    return {
                        "action": "chat",
                        "text_response": content[:4000],
                    }
            return {
                "action": "chat",
                "text_response": (
                    "I have no tool result to summarize. Please ask again "
                    "so I can call the right tool."
                ),
            }

        from app.core.tool_router import route_message

        decision = route_message(
            user_message,
            nodes,
            project_name=str(project.get("name") or "assistant"),
            project_description=str(project.get("description") or ""),
        )
        payload: Dict[str, Any] = {
            "action": decision.action,
            "text_response": decision.text_response,
            "debug_info": (
                f"tool_router strategy={decision.strategy} "
                f"confidence={decision.confidence} ranked={decision.ranked[:5]}"
            ),
        }
        if decision.action == "call_node" and decision.node_name:
            payload["node_name"] = decision.node_name
            payload["inputs"] = decision.inputs
        return payload


def _env_litellm_config() -> dict:
    """Build a single LiteLLM config from env for DB-less contexts."""
    from app.config import Config

    candidates = [
        ("gemini", os.getenv("GEMINI_API_KEY", ""), "gemini/gemini-2.0-flash", ""),
        ("groq", os.getenv("GROQ_API_KEY", ""), "groq/llama-3.3-70b-versatile", ""),
        (
            "openrouter",
            os.getenv("OPENROUTER_API_KEY", ""),
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "https://openrouter.ai/api/v1",
        ),
        (
            "github",
            os.getenv("GITHUB_TOKEN", ""),
            "github/gpt-4o-mini",
            "https://models.github.ai/inference",
        ),
        ("openai", os.getenv("OPENAI_API_KEY", ""), "gpt-4o-mini", ""),
    ]
    for provider, key, model, base_url in candidates:
        if key.strip():
            return {
                "provider": provider,
                "provider_id": f"env-{provider}",
                "model": os.getenv("LITELLM_MODEL", model),
                "api_key": key.strip(),
                "base_url": os.getenv("LITELLM_BASE_URL", base_url),
                "temperature": 0.4,
                "max_tokens": 1024,
                "top_p": 0.9,
                "timeout_seconds": float(getattr(Config, "LITELLM_TIMEOUT_SECONDS", 60)),
            }
    raise RuntimeError(
        "LLM_PROVIDER=litellm has no configured endpoint: add a provider via "
        "POST /api/llm-providers (DB) or set one of GEMINI_API_KEY / "
        "GROQ_API_KEY / OPENROUTER_API_KEY / GITHUB_TOKEN / OPENAI_API_KEY."
    )


class LLMFactory:
    """Factory to create appropriate LLM provider."""

    @staticmethod
    def create(provider_type: str = None) -> LLMProvider:
        """
        Create LLM provider.

        provider_type options:
        - "litellm": DB-driven cloud gateway (Gemini / Groq / OpenRouter /
                     GitHub Models / OpenAI / custom). Without DB it builds a
                     single env-based endpoint.
        - "openai": OpenAI GPT via OPENAI_API_KEY.
        - "keyword": lexical tool router (offline, no dependencies).
        """
        provider_type = (
            provider_type or os.getenv("LLM_PROVIDER", "litellm") or "litellm"
        ).lower()

        if provider_type in {"litellm", "auto", "database"}:
            from llm.litellm_provider import LiteLLMProvider

            return LiteLLMProvider(_env_litellm_config())

        if provider_type == "openai":
            return OpenAIProvider()

        return KeywordMatchProvider()
