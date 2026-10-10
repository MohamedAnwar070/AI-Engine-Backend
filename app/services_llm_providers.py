"""DB-driven LiteLLM provider management (CRUD + resolution + fallback chain).

Separation: this module owns MongoDB access for ``llm_providers``. The actual
model call lives in ``llm.litellm_provider.LiteLLMProvider`` (explicit config,
no DB import) so it stays unit-testable. ``app.services`` calls into this
module for chat-time resolution.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.config import Config
from app.core.errors import NotFoundError
from app.core.llm_catalog import get_catalog_entry
from app.core.secrets import decrypt_secret, encrypt_secret, mask_key
from app.database import llm_providers_collection, projects_collection
from app.schemas import (
    LLMProviderCreate,
    LLMProviderResponse,
    LLMProviderTestResponse,
    LLMProviderUpdate,
    ProjectResponse,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}
    return value


def serialize_document(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc is None:
        return None
    return serialize_value(doc)


def _public_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return a DB doc with the encrypted key replaced by hint flags."""
    out = dict(doc)
    enc = str(out.pop("api_key_encrypted", "") or "")
    has_key = bool(enc) or bool((out.get("api_key_env") or "").strip() and os.getenv(out["api_key_env"] or "", "").strip())
    out["has_api_key"] = has_key
    out["api_key_hint"] = ""
    if enc:
        try:
            out["api_key_hint"] = mask_key(decrypt_secret(enc))
        except Exception:
            out["api_key_hint"] = "****"
    elif (out.get("api_key_env") or "").strip():
        env_val = os.getenv(out["api_key_env"], "")
        out["api_key_hint"] = mask_key(env_val) + " (env)" if env_val else "(env, unset)"
    return out


def resolve_api_key(doc: Dict[str, Any]) -> str:
    """DB encrypted key wins; otherwise resolve api_key_env; else ''."""
    enc = str(doc.get("api_key_encrypted") or "")
    if enc:
        try:
            return decrypt_secret(enc)
        except Exception:
            return ""
    env_name = (doc.get("api_key_env") or "").strip()
    if env_name:
        return os.getenv(env_name, "").strip()
    return ""


def to_runtime_config(doc: Dict[str, Any], *, model_override: Optional[str] = None) -> Dict[str, Any]:
    return {
        "provider": doc.get("provider", "custom"),
        "provider_id": doc.get("provider_id", ""),
        "model": (model_override or "").strip() or doc.get("model", ""),
        "api_key": resolve_api_key(doc),
        "base_url": (doc.get("base_url") or "").strip(),
        "extra_params": doc.get("extra_params") or {},
        "auth_type": (doc.get("auth_type") or "bearer"),
        "auth_header": (doc.get("auth_header") or ""),
        "auth_query_param": (doc.get("auth_query_param") or ""),
        "api_format": (doc.get("api_format") or "chat-completions"),
        "temperature": float(doc.get("default_temperature") or 0.4),
        "max_tokens": int(doc.get("default_max_tokens") or 1024),
        "top_p": float(doc.get("default_top_p") or 0.9),
        "timeout_seconds": float(Config.LITELLM_TIMEOUT_SECONDS),
    }


def _requires_key(auth_type: str) -> bool:
    return (auth_type or "bearer").lower() != "none"


async def list_llm_providers(*, active_only: bool = False) -> List[LLMProviderResponse]:
    query: Dict[str, Any] = {"is_active": True} if active_only else {}
    docs = await llm_providers_collection.find(query).sort(
        [("is_default", -1), ("priority", 1), ("created_at", 1)]
    ).to_list(length=200)
    return [LLMProviderResponse.model_validate(serialize_document(_public_doc(d))) for d in docs]


async def get_llm_provider_doc(provider_id: str) -> Dict[str, Any]:
    doc = await llm_providers_collection.find_one({"provider_id": provider_id})
    if not doc:
        raise NotFoundError(f"LLM provider {provider_id} not found", code="llm_provider_not_found")
    return doc


async def get_llm_provider(provider_id: str) -> LLMProviderResponse:
    return LLMProviderResponse.model_validate(
        serialize_document(_public_doc(await get_llm_provider_doc(provider_id)))
    )


async def get_default_llm_provider_doc() -> Optional[Dict[str, Any]]:
    doc = await llm_providers_collection.find_one({"is_default": True, "is_active": True})
    if doc:
        return doc
    return await llm_providers_collection.find_one(
        {"is_active": True}, sort=[("priority", 1), ("created_at", 1)]
    )


async def get_ordered_active_provider_docs(limit: int = 5) -> List[Dict[str, Any]]:
    default = await get_default_llm_provider_doc()
    rest = await llm_providers_collection.find({"is_active": True}).sort(
        [("priority", 1), ("created_at", 1)]
    ).to_list(length=limit)
    ordered: List[Dict[str, Any]] = []
    if default:
        ordered.append(default)
    for doc in rest:
        if not ordered or doc.get("provider_id") != ordered[0].get("provider_id"):
            ordered.append(doc)
    return ordered[: max(1, limit)]


def _validate_model_for_provider(provider: str, model: str, base_url: str = "") -> None:
    model = (model or "").strip()
    if not model:
        raise ValueError("model is required")
    if provider in {"gemini", "groq", "openrouter", "github"} and "/" not in model:
        raise ValueError(
            f"model for provider '{provider}' must be a LiteLLM id "
            f"(e.g. gemini/gemini-2.0-flash, groq/llama-3.3-70b-versatile, "
            f"openrouter/<slug>:free, github/gpt-4o-mini). Got: {model!r}"
        )
    if provider == "custom":
        if not (base_url or "").strip():
            raise ValueError("base_url is required when provider is 'custom'")
        if not (base_url or "").strip().lower().startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")


async def create_llm_provider(payload: LLMProviderCreate) -> LLMProviderResponse:
    _validate_model_for_provider(payload.provider, payload.model, payload.base_url)
    now = utc_now()
    existing_default = await llm_providers_collection.find_one({"is_default": True})
    doc: Dict[str, Any] = {
        "provider_id": f"llmprov_{ObjectId()}",
        "name": payload.name,
        "provider": payload.provider,
        "model": payload.model,
        "base_url": payload.base_url,
        "api_key_env": payload.api_key_env,
        "api_key_encrypted": encrypt_secret(payload.api_key),
        "extra_params": payload.extra_params,
        "auth_type": payload.auth_type,
        "auth_header": payload.auth_header,
        "auth_query_param": payload.auth_query_param,
        "api_format": payload.api_format,
        "default_temperature": payload.default_temperature,
        "default_max_tokens": payload.default_max_tokens,
        "default_top_p": payload.default_top_p,
        "is_active": payload.is_active,
        "is_default": existing_default is None,
        "priority": payload.priority,
        "tier": payload.tier,
        "limits": payload.limits.model_dump(),
        "limits_note": payload.limits_note,
        "docs_url": payload.docs_url,
        "cost_per_1k_input_usd": payload.cost_per_1k_input_usd,
        "cost_per_1k_output_usd": payload.cost_per_1k_output_usd,
        "total_requests": 0,
        "total_errors": 0,
        "last_used_at": None,
        "last_tested_at": None,
        "last_test_status": None,
        "last_test_error": None,
        "last_latency_ms": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await llm_providers_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return LLMProviderResponse.model_validate(serialize_document(_public_doc(doc)))


async def update_llm_provider(provider_id: str, payload: LLMProviderUpdate) -> LLMProviderResponse:
    doc = await get_llm_provider_doc(provider_id)
    update: Dict[str, Any] = {}
    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("api_key", None)
    if api_key is not None:
        update["api_key_encrypted"] = encrypt_secret(api_key)
    for key, value in data.items():
        if key == "is_default":
            continue
        update[key] = value
    if "provider" in update or "model" in update or "base_url" in update:
        _validate_model_for_provider(
            update.get("provider", doc.get("provider")),
            update.get("model", doc.get("model")),
            update.get("base_url", doc.get("base_url", "")),
        )
    if update:
        update["updated_at"] = utc_now()
        await llm_providers_collection.update_one(
            {"provider_id": provider_id}, {"$set": update}
        )
    if data.get("is_default") is True:
        await llm_providers_collection.update_many(
            {"is_default": True}, {"$set": {"is_default": False}}
        )
        await llm_providers_collection.update_one(
            {"provider_id": provider_id},
            {"$set": {"is_default": True, "is_active": True, "updated_at": utc_now()}},
        )
    return await get_llm_provider(provider_id)


async def delete_llm_provider(provider_id: str) -> None:
    doc = await get_llm_provider_doc(provider_id)
    await llm_providers_collection.delete_one({"provider_id": provider_id})
    if doc.get("is_default"):
        nxt = await llm_providers_collection.find_one(
            {"is_active": True}, sort=[("priority", 1), ("created_at", 1)]
        )
        if nxt:
            await llm_providers_collection.update_one(
                {"provider_id": nxt["provider_id"]},
                {"$set": {"is_default": True, "updated_at": utc_now()}},
            )


async def set_default_llm_provider(provider_id: str) -> LLMProviderResponse:
    await get_llm_provider_doc(provider_id)
    await llm_providers_collection.update_many(
        {"is_default": True}, {"$set": {"is_default": False}}
    )
    await llm_providers_collection.update_one(
        {"provider_id": provider_id},
        {"$set": {"is_default": True, "is_active": True, "updated_at": utc_now()}},
    )
    return await get_llm_provider(provider_id)


async def record_llm_usage(provider_id: str, *, ok: bool) -> None:
    try:
        await llm_providers_collection.update_one(
            {"provider_id": provider_id},
            {
                "$inc": {"total_requests": 1, **({} if ok else {"total_errors": 1})},
                "$set": {"last_used_at": utc_now()},
            },
        )
    except Exception:
        pass


async def test_llm_provider(
    provider_id: str, *, prompt: str = "Reply with: ok", timeout_seconds: Optional[float] = None
) -> LLMProviderTestResponse:
    from llm.litellm_provider import LiteLLMProvider

    doc = await get_llm_provider_doc(provider_id)
    config = to_runtime_config(doc)
    if timeout_seconds:
        config["timeout_seconds"] = float(timeout_seconds)
    if _requires_key(str(doc.get("auth_type") or "bearer")) and not config["api_key"]:
        raise ValueError(
            f"Provider '{doc.get('name')}' has no API key: store api_key or set "
            f"env var {doc.get('api_key_env') or '(api_key_env)'}."
        )
    started = time.perf_counter()
    try:
        result = await LiteLLMProvider(config).test_connection(prompt=prompt)
        latency_ms = float(result["latency_ms"])
        await llm_providers_collection.update_one(
            {"provider_id": provider_id},
            {
                "$set": {
                    "last_tested_at": utc_now(),
                    "last_test_status": "ok",
                    "last_test_error": None,
                    "last_latency_ms": latency_ms,
                }
            },
        )
        return LLMProviderTestResponse(
            provider_id=provider_id,
            ok=True,
            latency_ms=latency_ms,
            model=config["model"],
            sample_text=str(result.get("sample_text") or ""),
            tested_at=utc_now(),
        )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        await llm_providers_collection.update_one(
            {"provider_id": provider_id},
            {
                "$set": {
                    "last_tested_at": utc_now(),
                    "last_test_status": "error",
                    "last_test_error": str(exc)[:2000],
                    "last_latency_ms": latency_ms,
                }
            },
        )
        return LLMProviderTestResponse(
            provider_id=provider_id,
            ok=False,
            latency_ms=latency_ms,
            model=config["model"],
            error=str(exc)[:2000],
            tested_at=utc_now(),
        )


async def test_unsaved_llm_config(payload: LLMProviderCreate, *, prompt: str = "Reply with: ok") -> LLMProviderTestResponse:
    """Test a config without persisting (UI 'Test before save')."""
    from llm.litellm_provider import LiteLLMProvider

    _validate_model_for_provider(payload.provider, payload.model, payload.base_url)
    api_key = payload.api_key.strip() or os.getenv(payload.api_key_env or "", "").strip()
    if _requires_key(payload.auth_type) and not api_key:
        raise ValueError("No API key: provide api_key or a set api_key_env var.")
    config = {
        "provider": payload.provider,
        "provider_id": "unsaved",
        "model": payload.model,
        "api_key": api_key,
        "base_url": payload.base_url,
        "extra_params": payload.extra_params,
        "auth_type": payload.auth_type,
        "auth_header": payload.auth_header,
        "auth_query_param": payload.auth_query_param,
        "api_format": payload.api_format,
        "temperature": payload.default_temperature,
        "max_tokens": 64,
        "top_p": payload.default_top_p,
        "timeout_seconds": float(Config.LITELLM_TIMEOUT_SECONDS),
    }
    started = time.perf_counter()
    try:
        result = await LiteLLMProvider(config).test_connection(prompt=prompt)
        return LLMProviderTestResponse(
            provider_id="unsaved",
            ok=True,
            latency_ms=float(result["latency_ms"]),
            model=payload.model,
            sample_text=str(result.get("sample_text") or ""),
            tested_at=utc_now(),
        )
    except Exception as exc:
        return LLMProviderTestResponse(
            provider_id="unsaved",
            ok=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            model=payload.model,
            error=str(exc)[:2000],
            tested_at=utc_now(),
        )


async def list_models_for_provider(provider_id: str) -> List[str]:
    """Discover model ids via GET {base_url}/models (custom endpoints)."""
    from llm.litellm_provider import LiteLLMProvider

    doc = await get_llm_provider_doc(provider_id)
    return await LiteLLMProvider(to_runtime_config(doc)).list_models()


async def list_provider_projects(provider_id: str) -> List[ProjectResponse]:
    """Projects bound to this provider (chat uses ONLY this provider for them)."""
    await get_llm_provider_doc(provider_id)
    docs = await projects_collection.find(
        {"llm_config.provider_id": provider_id}
    ).sort("name", 1).to_list(length=200)
    return [ProjectResponse.model_validate(serialize_document(d)) for d in docs]


async def bind_project_to_provider(
    provider_id: str, project_api_id: str
) -> ProjectResponse:
    """Pin a project to a provider: its chat uses ONLY this provider row."""
    doc = await get_llm_provider_doc(provider_id)
    if not doc.get("is_active"):
        raise ValueError(f"Provider '{doc.get('name')}' is disabled; enable it first.")
    project = await projects_collection.find_one({"project_api_id": project_api_id})
    if not project:
        raise NotFoundError(
            f"Project {project_api_id} not found", code="project_not_found"
        )
    llm_config = dict(project.get("llm_config") or {})
    llm_config["provider_id"] = provider_id
    await projects_collection.update_one(
        {"project_api_id": project_api_id},
        {"$set": {"llm_config": llm_config, "updated_at": utc_now()}},
    )
    updated = await projects_collection.find_one({"project_api_id": project_api_id})
    return ProjectResponse.model_validate(serialize_document(updated))


async def unbind_project_from_provider(
    provider_id: str, project_api_id: str
) -> ProjectResponse:
    """Release a project back to the global default chain (idempotent)."""
    await get_llm_provider_doc(provider_id)
    project = await projects_collection.find_one({"project_api_id": project_api_id})
    if not project:
        raise NotFoundError(
            f"Project {project_api_id} not found", code="project_not_found"
        )
    llm_config = dict(project.get("llm_config") or {})
    if (llm_config.get("provider_id") or "") != provider_id:
        return ProjectResponse.model_validate(serialize_document(project))
    llm_config["provider_id"] = None
    await projects_collection.update_one(
        {"project_api_id": project_api_id},
        {"$set": {"llm_config": llm_config, "updated_at": utc_now()}},
    )
    updated = await projects_collection.find_one({"project_api_id": project_api_id})
    return ProjectResponse.model_validate(serialize_document(updated))


async def resolve_providers_for_project(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ordered provider docs for a chat: project binding wins, else default chain."""
    llm_config = project.get("llm_config") or {}
    if isinstance(llm_config, dict):
        provider_id = (llm_config.get("provider_id") or "").strip()
        if provider_id:
            try:
                doc = await get_llm_provider_doc(provider_id)
                if doc.get("is_active"):
                    return [doc]
            except NotFoundError:
                pass
    return await get_ordered_active_provider_docs(limit=max(1, Config.LITELLM_NUM_FALLBACKS))


async def ensure_default_llm_providers() -> None:
    """Seed DB from env keys on first boot (idempotent, never overwrites)."""
    try:
        count = await llm_providers_collection.count_documents({})
    except Exception:
        return
    if count:
        return
    presets = [
        ("Gemini Flash (env)", "gemini", "gemini/gemini-2.0-flash", "GEMINI_API_KEY", "", 10),
        ("Groq Llama (env)", "groq", "groq/llama-3.3-70b-versatile", "GROQ_API_KEY", "", 20),
        (
            "OpenRouter Free (env)",
            "openrouter",
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "OPENROUTER_API_KEY",
            "https://openrouter.ai/api/v1",
            30,
        ),
        (
            "GitHub Models (env)",
            "github",
            "github/gpt-4o-mini",
            "GITHUB_TOKEN",
            "https://models.github.ai/inference",
            40,
        ),
        ("OpenAI (env)", "openai", "gpt-4o-mini", "OPENAI_API_KEY", "", 50),
    ]
    now = utc_now()
    first = True
    for name, provider, model, env_var, base_url, priority in presets:
        if not os.getenv(env_var, "").strip():
            continue
        catalog_note = ""
        entry = get_catalog_entry(
            {
                "gemini": "gemini-flash-free",
                "groq": "groq-llama-free",
                "openrouter": "openrouter-free",
                "github": "github-models-free",
                "openai": "openai-paid",
            }[provider]
        )
        if entry:
            catalog_note = str(entry.get("limits_note") or "")
        await llm_providers_collection.insert_one(
            {
                "provider_id": f"llmprov_{ObjectId()}",
                "name": name,
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "api_key_env": env_var,
                "api_key_encrypted": "",
                "extra_params": {},
                "default_temperature": 0.4,
                "default_max_tokens": 1024,
                "default_top_p": 0.9,
                "is_active": True,
                "is_default": first,
                "priority": priority,
                "tier": "paid" if provider == "openai" else "free",
                "limits": {"rpm": None, "rpd": None, "tpm": None, "context_window": None},
                "limits_note": catalog_note,
                "docs_url": str((entry or {}).get("docs_url") or ""),
                "cost_per_1k_input_usd": None,
                "cost_per_1k_output_usd": None,
                "total_requests": 0,
                "total_errors": 0,
                "last_used_at": None,
                "last_tested_at": None,
                "last_test_status": None,
                "last_test_error": None,
                "last_latency_ms": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        first = False
