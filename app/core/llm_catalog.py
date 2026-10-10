"""Static LiteLLM provider catalog (presets + verification links).

Free-tier limits change frequently and differ per account/region, so this
module deliberately does NOT hardcode RPM/RPD/TPM numbers. Each entry carries
``limits_note`` + ``docs_url`` + ``console_url`` telling the operator where to
verify current quotas. Editable ``limits`` live on the DB document
(``llm_providers`` collection) so ops can record what they verified without a
redeploy.

Model ids use LiteLLM naming:
  gemini/...      -> Google AI Studio (GEMINI_API_KEY)
  groq/...        -> GroqCloud (GROQ_API_KEY)
  openrouter/...  -> OpenRouter (OPENROUTER_API_KEY, base_url preset)
  github/...      -> GitHub Models (GITHUB_TOKEN, models.github.ai base_url)
  gpt-...         -> OpenAI (OPENAI_API_KEY)
  custom          -> any OpenAI-compatible endpoint (operator base_url + key)
"""

from __future__ import annotations

from typing import Any, Dict, List

CatalogEntry = Dict[str, Any]

GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

CATALOG: List[CatalogEntry] = [
    {
        "key": "gemini-flash-free",
        "label": "Gemini Flash (free tier)",
        "provider": "gemini",
        "model": "gemini/gemini-2.0-flash",
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "",
        "tier": "free",
        "suggested_temperature": 0.4,
        "suggested_max_tokens": 1024,
        "limits_note": (
            "Google AI Studio free tier exists (RPM/RPD/TPM quotas per model, "
            "rate-limited per project). Quotas change and vary by region/model "
            "— verify in Google AI Studio quotas page before relying on them."
        ),
        "docs_url": "https://ai.google.dev/gemini-api/docs/rate-limits",
        "console_url": "https://aistudio.google.com/",
        "get_key_url": "https://aistudio.google.com/app/apikey",
    },
    {
        "key": "groq-llama-free",
        "label": "Groq Llama (free tier)",
        "provider": "groq",
        "model": "groq/llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "base_url": "",
        "tier": "free",
        "suggested_temperature": 0.3,
        "suggested_max_tokens": 1024,
        "limits_note": (
            "GroqCloud free tier is rate-limited per model (requests/min, "
            "tokens/min, requests/day). Limits differ per model and change — "
            "verify in GroqCloud console limits page."
        ),
        "docs_url": "https://console.groq.com/docs/rate-limits",
        "console_url": "https://console.groq.com/",
        "get_key_url": "https://console.groq.com/keys",
    },
    {
        "key": "openrouter-free",
        "label": "OpenRouter (:free models)",
        "provider": "openrouter",
        "model": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": OPENROUTER_BASE_URL,
        "tier": "free",
        "suggested_temperature": 0.4,
        "suggested_max_tokens": 1024,
        "limits_note": (
            "OpenRouter ':free' models are free but rate-limited (requests/day "
            "when no credits, stricter without payment method). Paid credits "
            "raise limits. Verify current policy in OpenRouter docs/limits."
        ),
        "docs_url": "https://openrouter.ai/docs/limits",
        "console_url": "https://openrouter.ai/keys",
        "get_key_url": "https://openrouter.ai/keys",
    },
    {
        "key": "github-models-free",
        "label": "GitHub Models (free playground)",
        "provider": "github",
        "model": "github/gpt-4o-mini",
        "api_key_env": "GITHUB_TOKEN",
        "base_url": GITHUB_MODELS_BASE_URL,
        "tier": "free",
        "suggested_temperature": 0.3,
        "suggested_max_tokens": 1024,
        "limits_note": (
            "GitHub Models free usage is tied to your GitHub account (low "
            "rate limits, playground/eval scope, not for high-volume prod). "
            "Paid use goes through Azure AI Foundry. Verify rate limits in "
            "GitHub docs — they change during preview."
        ),
        "docs_url": "https://docs.github.com/en/github-models",
        "console_url": "https://github.com/marketplace/models",
        "get_key_url": "https://github.com/settings/tokens",
    },
    {
        "key": "openai-paid",
        "label": "OpenAI (paid)",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "",
        "tier": "paid",
        "suggested_temperature": 0.4,
        "suggested_max_tokens": 1024,
        "limits_note": (
            "Pay-as-you-go with tiered rate limits (tier rises with spend). "
            "Verify TPM/RPM for your tier and set billing limits in console."
        ),
        "docs_url": "https://platform.openai.com/docs/guides/rate-limits",
        "console_url": "https://platform.openai.com/settings/organization/limits",
        "get_key_url": "https://platform.openai.com/api-keys",
    },
    {
        "key": "custom-openai-compatible",
        "label": "Custom OpenAI-compatible (paid/self-hosted)",
        "provider": "custom",
        "model": "openai/your-model-id",
        "api_key_env": "",
        "base_url": "https://your-gateway.example.com/v1",
        "tier": "paid",
        "suggested_temperature": 0.4,
        "suggested_max_tokens": 1024,
        "limits_note": (
            "Any OpenAI-compatible gateway (vLLM, Ollama, Together, DeepSeek, "
            "Mistral, Azure OpenAI, Bedrock via LiteLLM, ...). Limits/costs "
            "are defined by that endpoint — record them on the DB document."
        ),
        "docs_url": "https://docs.litellm.ai/docs/providers",
        "console_url": "",
        "get_key_url": "",
    },
]

SUPPORTED_PROVIDERS = sorted({entry["provider"] for entry in CATALOG})


def get_catalog() -> List[CatalogEntry]:
    return [dict(entry) for entry in CATALOG]


def get_catalog_entry(key: str) -> CatalogEntry | None:
    for entry in CATALOG:
        if entry["key"] == key:
            return dict(entry)
    return None
