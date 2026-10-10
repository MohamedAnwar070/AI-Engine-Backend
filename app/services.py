from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.config import Config
from app.auth import ensure_default_admin, write_governance_audit
from app.core.errors import InternalServerError, NotFoundError
from app.core.logging import get_logger
from app.core.orchestration import build_orchestration_backend
from app.database import (
    client,
    conversations_collection,
    ensure_indexes,
    governance_audits_collection,
    node_executions_collection,
    node_versions_collection,
    nodes_collection,
    prompt_presets_collection,
    project_templates_collection,
    projects_collection,
    test_runs_collection,
    tool_audits_collection,
)
from app.runtime import build_default_runtime_registry
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ControlPlaneOverviewResponse,
    ConversationRecoveryResponse,
    ConversationQualityMetric,
    ConversationResponse,
    ExecutionHistoryResponse,
    ExecutionMetadata,
    FailedNodeMetric,
    GraphEdgeResponse,
    GraphNodeResponse,
    GovernanceAuditResponse,
    HttpToolConfig,
    LLMConfig,
    MessageResponse,
    NodeCreate,
    NodeExecutionResponse,
    NodeGraphResponse,
    NodeResponse,
    NodeSchema,
    NodeVersionResponse,
    ProjectBundle,
    ProjectBundleImportResult,
    ProjectBundleNode,
    ProjectBundleProject,
    ProjectHealthMetric,
    ProjectTemplateCreate,
    ProjectTemplateUpdate,
    ProjectTemplateResponse,
    ProjectCreate,
    PromptPresetCreate,
    PromptPresetResponse,
    ProjectResponse,
    ProjectTestQueuedResponse,
    ProjectTestRequest,
    ProjectTestResponse,
    ProjectTestResult,
    ProjectTestRouteResponse,
    ProjectTestRunResponse,
    ReplayExecutionRequest,
    SimulationIssue,
    SimulationResponse,
    StatsResponse,
    ToolAuditResponse,
    WorkspaceImportResult,
)
from llm import LLMFactory


logger = get_logger(__name__)
_llm_provider = None
_runtime_registry = build_default_runtime_registry()
_test_run_workers: List[asyncio.Task] = []
_orchestration = build_orchestration_backend(Config.REDIS_URL)
_llm_status = "unknown"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}
    return value


def serialize_document(document: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if document is None:
        return None
    return serialize_value(document)


def project_to_document(project: ProjectCreate) -> Dict[str, Any]:
    now = utc_now()
    return {
        "project_api_id": f"proj_{ObjectId()}",
        "name": project.name,
        "description": project.description,
        "llm_prompt": project.llm_prompt,
        "llm_config": project.llm_config.model_dump(by_alias=True),
        "created_at": now,
        "updated_at": now,
    }


def node_to_document(node: NodeCreate) -> Dict[str, Any]:
    now = utc_now()
    return {
        "node_id": f"node_{ObjectId()}",
        "project_api_id": node.project_api_id,
        "name": node.name,
        "description": node.description,
        "node_schema": node.node_schema.model_dump(by_alias=True),
        "runtime": node.runtime,
        "tool_code": node.tool_code or "",
        "http_config": (
            node.http_config.model_dump(by_alias=True) if node.http_config else None
        ),
        "is_root": node.is_root,
        "dependencies": node.dependencies,
        "created_at": now,
        "updated_at": now,
    }


async def _get_template_document(template_id: str) -> Dict[str, Any]:
    template = await project_templates_collection.find_one({"template_id": template_id})
    if not template:
        raise NotFoundError(
            f"Template {template_id} not found", code="template_not_found"
        )
    return template


def _render_template_value(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        rendered = value
        for key, variable in variables.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(variable))
        return rendered
    if isinstance(value, list):
        return [_render_template_value(item, variables) for item in value]
    if isinstance(value, dict):
        return {
            key: _render_template_value(item, variables) for key, item in value.items()
        }
    return value


async def _apply_project_template(
    project_document: Dict[str, Any], template_id: str, variables: Dict[str, Any]
) -> None:
    template = await _get_template_document(template_id)
    for node_definition in template.get("suggested_nodes", []):
        rendered_node = _render_template_value(node_definition, variables)
        if not str(rendered_node.get("tool_code", "")).strip():
            raise InternalServerError(
                f"Template node {rendered_node.get('name', 'unknown')} is missing tool_code",
                code="template_node_missing_tool_code",
            )
        node_document = {
            "node_id": f"node_{ObjectId()}",
            "project_api_id": project_document["project_api_id"],
            "name": rendered_node["name"],
            "description": rendered_node.get("description", ""),
            "node_schema": rendered_node.get(
                "node_schema", {"inputs": {}, "outputs": {}}
            ),
            "tool_code": rendered_node.get("tool_code"),
            "is_root": rendered_node.get("is_root", False),
            "dependencies": rendered_node.get("dependencies", []),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        result = await nodes_collection.insert_one(node_document)
        node_document["_id"] = result.inserted_id
        await _create_node_version(node_document, source="manual")


async def _create_node_version(document: Dict[str, Any], *, source: str) -> None:
    latest = await node_versions_collection.find_one(
        {"node_id": document["node_id"]},
        sort=[("version_number", -1)],
    )
    version_number = 1 if not latest else int(latest.get("version_number", 0)) + 1
    await node_versions_collection.insert_one(
        {
            "version_id": f"nodever_{ObjectId()}",
            "node_id": document["node_id"],
            "project_api_id": document["project_api_id"],
            "version_number": version_number,
            "name": document["name"],
            "description": document["description"],
            "node_schema": document.get("node_schema", {}),
            "runtime": document.get("runtime", "javascript"),
            "tool_code": document.get("tool_code") or "",
            "http_config": document.get("http_config"),
            "is_root": document.get("is_root", False),
            "dependencies": document.get("dependencies", []),
            "created_at": utc_now(),
            "source": source,
        }
    )


def _confidence_score(user_message: str, node: Optional[Dict[str, Any]]) -> float:
    """Domain-agnostic confidence from lexical tool router."""
    if not node:
        return 0.1
    from app.core.tool_router import score_node

    raw = score_node(user_message, node)
    return round(min(1.0, raw / 10.0), 2)


def _find_root_fallback_node(
    nodes: List[Dict[str, Any]], user_message: str
) -> Optional[tuple[Dict[str, Any], Dict[str, Any]]]:
    """Prefer generic router; fall back to is_root entrypoint."""
    from app.core.tool_router import extract_inputs, route_message

    decision = route_message(user_message, nodes, min_score=2.0)
    if decision.action == "call_node" and decision.node_name:
        node = next(
            (n for n in nodes if n.get("name") == decision.node_name), None
        )
        if node:
            return node, decision.inputs or extract_inputs(user_message, node)

    root_node = next((node for node in nodes if node.get("is_root")), None)
    if not root_node:
        return None
    return root_node, extract_inputs(user_message, root_node)


async def _create_tool_audit(
    *,
    project_api_id: str,
    user_message: str,
    llm_action: str,
    node: Optional[Dict[str, Any]],
    conversation_id: Optional[str],
    fallback_used: bool,
    recovery_used: bool,
    reason: Optional[str],
) -> ToolAuditResponse:
    payload = {
        "audit_id": f"audit_{ObjectId()}",
        "project_api_id": project_api_id,
        "conversation_id": conversation_id,
        "user_message": user_message,
        "llm_action": llm_action,
        "node_name": node.get("name") if node else None,
        "confidence_score": _confidence_score(user_message, node),
        "fallback_used": fallback_used,
        "recovery_used": recovery_used,
        "reason": reason,
        "created_at": utc_now(),
    }
    result = await tool_audits_collection.insert_one(payload)
    payload["_id"] = result.inserted_id
    return ToolAuditResponse.model_validate(serialize_document(payload))


async def startup_background_services() -> None:
    global _orchestration

    try:
        await ensure_indexes()
    except RuntimeError:
        logger.warning(
            "Skipping MongoDB index setup in the current event loop",
            extra={
                "event": "mongodb_index_setup_skipped",
                "error_code": "mongodb_index_setup_skipped",
            },
        )
    except Exception:
        logger.exception(
            "Failed to ensure MongoDB indexes during startup",
            extra={
                "event": "mongodb_index_setup_failed",
                "error_code": "mongodb_index_setup_failed",
            },
        )
    try:
        await ensure_default_admin()
    except Exception:
        logger.exception(
            "Failed to ensure default admin",
            extra={
                "event": "default_admin_setup_failed",
                "error_code": "default_admin_setup_failed",
            },
        )
    try:
        from app.services_llm_providers import ensure_default_llm_providers

        await ensure_default_llm_providers()
    except Exception:
        logger.exception(
            "Failed to seed LLM providers",
            extra={
                "event": "llm_provider_seed_failed",
                "error_code": "llm_provider_seed_failed",
            },
        )
    try:
        await _orchestration.startup()
    except Exception:
        logger.exception(
            "Failed to start configured orchestration backend; falling back to in-memory backend",
            extra={
                "event": "orchestration_startup_failed",
                "error_code": "orchestration_startup_failed",
            },
        )
        _orchestration = build_orchestration_backend(None)
        await _orchestration.startup()
    await _runtime_registry.start()
    # Probe LLM provider (keyword fallback on failure — does not fail boot)
    try:
        get_llm_provider()
    except Exception as exc:
        global _llm_status
        _llm_status = f"degraded: {exc}"
        logger.warning(
            "LLM provider not ready at startup",
            extra={
                "event": "llm_provider_degraded",
                "error_code": "llm_provider_degraded",
            },
        )
    if _test_run_workers:
        return
    for index in range(max(Config.TEST_QUEUE_WORKERS, 1)):
        task = asyncio.create_task(_test_run_worker(f"test-worker-{index + 1}"))
        _test_run_workers.append(task)


async def shutdown_background_services() -> None:
    for task in _test_run_workers:
        task.cancel()
    if _test_run_workers:
        await asyncio.gather(*_test_run_workers, return_exceptions=True)
    _test_run_workers.clear()
    await _runtime_registry.stop()
    await _orchestration.shutdown()


async def _test_run_worker(worker_name: str) -> None:
    while True:
        item = await _orchestration.dequeue_test_run(timeout_seconds=1)
        if not item:
            await asyncio.sleep(0.1)
            continue
        test_run_id, project_api_id, request = item
        lock_acquired = await _orchestration.acquire_test_run_lock(
            test_run_id, worker_name
        )
        if not lock_acquired:
            continue
        try:
            await test_runs_collection.update_one(
                {"test_run_id": test_run_id},
                {
                    "$set": {
                        "status": "running",
                        "started_at": utc_now(),
                        "worker_name": worker_name,
                    }
                },
            )
            result = await run_project_tests_sync(
                project_api_id, request, test_run_id=test_run_id
            )
            await test_runs_collection.update_one(
                {"test_run_id": test_run_id},
                {
                    "$set": {
                        "status": "completed",
                        "successful_tests": result.successful_tests,
                        "failed_tests": result.failed_tests,
                        "conversation_id": result.conversation_id,
                        "completed_at": result.completed_at,
                        "duration_ms": result.duration_ms,
                        "result": result.model_dump(mode="json"),
                    }
                },
            )
        except Exception as exc:
            logger.exception(
                "Queued project test run failed",
                extra={"event": "test_run_failed", "error_code": "test_run_failed"},
            )
            await test_runs_collection.update_one(
                {"test_run_id": test_run_id},
                {
                    "$set": {
                        "status": "failed",
                        "completed_at": utc_now(),
                        "error": str(exc),
                    }
                },
            )
        finally:
            await _orchestration.release_test_run_lock(test_run_id, worker_name)


def get_llm_provider():
    """Legacy sync singleton (keyword/openai).

    When LLM_PROVIDER=litellm (DB-driven, the production default), chat does
    NOT use this singleton — see generate_llm_response() fallback chain.
    This probe only verifies env-based boot and never fails startup.
    """
    global _llm_provider, _llm_status

    if _llm_provider is not None:
        return _llm_provider

    logger.info(
        "Initializing LLM provider",
        extra={"event": "llm_provider_init", "node_name": Config.LLM_PROVIDER},
    )
    try:
        _llm_provider = LLMFactory.create(Config.LLM_PROVIDER)
        _llm_status = "ready"
    except Exception:
        logger.exception(
            "Failed to initialize configured LLM provider; falling back to keyword",
            extra={
                "event": "llm_provider_fallback",
                "error_code": "llm_provider_init_failed",
            },
        )
        _llm_provider = LLMFactory.create("keyword")
        _llm_status = "degraded:keyword_fallback"
    return _llm_provider


async def create_project(project: ProjectCreate) -> ProjectResponse:
    document = project_to_document(project)
    result = await projects_collection.insert_one(document)
    document["_id"] = result.inserted_id
    if project.template_id:
        await _apply_project_template(
            document, project.template_id, project.template_variables
        )
        await write_governance_audit(
            "apply_project_template",
            "project_template",
            project.template_id,
            details={"project_api_id": document["project_api_id"]},
        )
    logger.info(
        "Project created",
        extra={
            "event": "project_created",
            "project_api_id": document["project_api_id"],
        },
    )
    return ProjectResponse.model_validate(serialize_document(document))


def _portable_project_dict(project: Dict[str, Any]) -> Dict[str, Any]:
    llm_config = project.get("llm_config") or {}
    if hasattr(llm_config, "model_dump"):
        llm_config = llm_config.model_dump(by_alias=True)
    return {
        "name": project.get("name", "Imported Project"),
        "description": project.get("description") or "Imported from package",
        "llm_prompt": project.get("llm_prompt") or "You are a helpful assistant.",
        "llm_config": llm_config
        or {
            "model_name": "gpt-4o-mini",
            "temperature": 0.7,
            "max_tokens": 2048,
            "top_p": 0.9,
            "top_k": 40,
        },
    }


def _portable_node_dict(node: Dict[str, Any]) -> Dict[str, Any]:
    schema = node.get("node_schema") or node.get("schema") or {
        "inputs": {},
        "outputs": {},
    }
    runtime = (node.get("runtime") or "javascript").lower()
    payload: Dict[str, Any] = {
        "name": node.get("name"),
        "description": node.get("description") or node.get("name") or "node",
        "node_schema": schema,
        "runtime": runtime,
        "tool_code": node.get("tool_code") or "",
        "http_config": node.get("http_config"),
        "is_root": bool(node.get("is_root", False)),
        "dependencies": node.get("dependencies") or [],
    }
    return payload


def normalize_package_entries(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Normalize any supported package shape into a list of:
    { "project": {...}, "nodes": [...] }
    """
    if not isinstance(raw, dict):
        raise ValueError("Package must be a JSON object")

    entries: List[Dict[str, Any]] = []

    # Multi-project workspace
    if isinstance(raw.get("projects"), list) and raw["projects"]:
        for item in raw["projects"]:
            if not isinstance(item, dict):
                continue
            # shapes: {project, nodes} or nested project fields at top
            if "project" in item:
                project = item.get("project") or {}
                nodes = item.get("nodes") or []
            else:
                project = item
                nodes = item.get("nodes") or []
            if isinstance(project, dict) and project.get("name"):
                entries.append(
                    {
                        "project": _portable_project_dict(project),
                        "nodes": [
                            _portable_node_dict(n)
                            for n in nodes
                            if isinstance(n, dict) and n.get("name")
                        ],
                    }
                )
        if entries:
            return entries

    # Single project package
    project = raw.get("project")
    nodes = raw.get("nodes") or []
    if isinstance(project, dict) and project.get("name"):
        return [
            {
                "project": _portable_project_dict(project),
                "nodes": [
                    _portable_node_dict(n)
                    for n in nodes
                    if isinstance(n, dict) and n.get("name")
                ],
            }
        ]

    # Bare project-like object with nodes
    if raw.get("name") and (raw.get("llm_prompt") is not None or raw.get("nodes")):
        return [
            {
                "project": _portable_project_dict(raw),
                "nodes": [
                    _portable_node_dict(n)
                    for n in (raw.get("nodes") or [])
                    if isinstance(n, dict) and n.get("name")
                ],
            }
        ]

    raise ValueError(
        "Invalid package: expected { project, nodes } or { projects: [{ project, nodes }] }"
    )


async def export_project_bundle(project_api_id: str) -> Dict[str, Any]:
    project = await get_project_document(project_api_id)
    nodes = await get_nodes_by_project_document(project_api_id)
    portable_nodes = []
    for node in nodes:
        n = _normalize_node_document(node)
        portable_nodes.append(
            {
                "name": n["name"],
                "description": n.get("description", ""),
                "node_schema": n.get("node_schema") or {"inputs": {}, "outputs": {}},
                "runtime": n.get("runtime") or "javascript",
                "tool_code": n.get("tool_code") or "",
                "http_config": n.get("http_config"),
                "is_root": bool(n.get("is_root", False)),
                "dependencies": n.get("dependencies") or [],
            }
        )
    return {
        "version": 2,
        "kind": "perfect_engine_project",
        "exported_at": utc_now().isoformat(),
        "meta": {
            "source_project_api_id": project.get("project_api_id"),
            "format": "perfect_engine_package_v2",
        },
        "project": _portable_project_dict(project),
        "nodes": portable_nodes,
    }


async def export_workspace_bundle() -> Dict[str, Any]:
    projects = await list_projects()
    entries = []
    for project in projects:
        bundle = await export_project_bundle(project.project_api_id)
        entries.append({"project": bundle["project"], "nodes": bundle["nodes"]})
    return {
        "version": 2,
        "kind": "perfect_engine_workspace",
        "exported_at": utc_now().isoformat(),
        "meta": {"format": "perfect_engine_package_v2", "project_count": len(entries)},
        "projects": entries,
    }


async def import_project_entry(
    project_payload: Dict[str, Any], nodes_payload: List[Dict[str, Any]]
) -> ProjectBundleImportResult:
    project = ProjectCreate(
        name=project_payload["name"],
        description=project_payload["description"],
        llm_prompt=project_payload["llm_prompt"],
        llm_config=project_payload.get("llm_config") or LLMConfig(),
    )
    created = await create_project(project)

    nodes_created = 0
    nodes_skipped = 0
    node_errors: List[str] = []

    # Import non-root first, then roots (stable dependency order is optional)
    ordered = sorted(
        nodes_payload, key=lambda n: (1 if n.get("is_root") else 0, n.get("name") or "")
    )

    for raw_node in ordered:
        try:
            node = ProjectBundleNode.model_validate(raw_node)
            await create_node(
                NodeCreate(
                    project_api_id=created.project_api_id,
                    name=node.name,
                    description=node.description,
                    node_schema=node.node_schema,
                    runtime=node.runtime,
                    tool_code=node.tool_code or "",
                    http_config=node.http_config,
                    is_root=node.is_root,
                    dependencies=node.dependencies,
                )
            )
            nodes_created += 1
        except Exception as exc:
            nodes_skipped += 1
            name = raw_node.get("name") if isinstance(raw_node, dict) else "?"
            node_errors.append(f"{name}: {exc}")
            logger.warning(
                "Node import skipped",
                extra={
                    "event": "package_node_import_skipped",
                    "node_name": name,
                    "error": str(exc),
                },
            )

    return ProjectBundleImportResult(
        project_api_id=created.project_api_id,
        name=created.name,
        nodes_created=nodes_created,
        nodes_skipped=nodes_skipped,
        node_errors=node_errors,
    )


async def import_package(raw: Dict[str, Any]) -> WorkspaceImportResult:
    entries = normalize_package_entries(raw)
    results: List[ProjectBundleImportResult] = []
    for entry in entries:
        result = await import_project_entry(entry["project"], entry["nodes"])
        results.append(result)
    return WorkspaceImportResult(projects_imported=len(results), results=results)


async def get_project_document(project_api_id: str) -> Dict[str, Any]:
    project = await projects_collection.find_one({"project_api_id": project_api_id})
    if not project:
        raise NotFoundError(
            f"Project {project_api_id} not found", code="project_not_found"
        )
    return project


async def get_project(project_api_id: str) -> ProjectResponse:
    return ProjectResponse.model_validate(
        serialize_document(await get_project_document(project_api_id))
    )


async def list_projects() -> List[ProjectResponse]:
    projects = (
        await projects_collection.find({}).sort("created_at", -1).to_list(length=200)
    )
    return [
        ProjectResponse.model_validate(serialize_document(project))
        for project in projects
    ]


async def update_project(
    project_api_id: str, project: ProjectCreate
) -> ProjectResponse:
    result = await projects_collection.update_one(
        {"project_api_id": project_api_id},
        {
            "$set": {
                "name": project.name,
                "description": project.description,
                "llm_prompt": project.llm_prompt,
                "llm_config": project.llm_config.model_dump(by_alias=True),
                "updated_at": utc_now(),
            }
        },
    )
    if result.matched_count == 0:
        raise NotFoundError(
            f"Project {project_api_id} not found", code="project_not_found"
        )
    return await get_project(project_api_id)


async def delete_project(project_api_id: str) -> MessageResponse:
    await nodes_collection.delete_many({"project_api_id": project_api_id})
    await conversations_collection.delete_many({"project_api_id": project_api_id})
    await node_executions_collection.delete_many({"project_api_id": project_api_id})
    await test_runs_collection.delete_many({"project_api_id": project_api_id})
    result = await projects_collection.delete_one({"project_api_id": project_api_id})
    if result.deleted_count == 0:
        raise NotFoundError(
            f"Project {project_api_id} not found", code="project_not_found"
        )
    return MessageResponse(message="Project deleted successfully")


async def create_node(node: NodeCreate) -> NodeResponse:
    await get_project_document(node.project_api_id)
    if node.is_root:
        await nodes_collection.update_many(
            {"project_api_id": node.project_api_id, "is_root": True},
            {"$set": {"is_root": False, "updated_at": utc_now()}},
        )
    document = node_to_document(node)
    result = await nodes_collection.insert_one(document)
    document["_id"] = result.inserted_id
    await _create_node_version(document, source="create")
    return NodeResponse.model_validate(serialize_document(document))


async def get_node_document_by_id(node_id: str) -> Dict[str, Any]:
    node = await nodes_collection.find_one({"node_id": node_id})
    if not node:
        raise NotFoundError(f"Node {node_id} not found", code="node_not_found")
    return node


def _normalize_node_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill multi-runtime fields for legacy nodes."""
    normalized = dict(document)
    normalized.setdefault("runtime", "javascript")
    normalized.setdefault("tool_code", normalized.get("tool_code") or "")
    normalized.setdefault("http_config", None)
    return normalized


async def get_node(node_id: str) -> NodeResponse:
    return NodeResponse.model_validate(
        serialize_document(
            _normalize_node_document(await get_node_document_by_id(node_id))
        )
    )


async def get_nodes_by_project_document(project_api_id: str) -> List[Dict[str, Any]]:
    return (
        await nodes_collection.find({"project_api_id": project_api_id})
        .sort("created_at", 1)
        .to_list(length=500)
    )


async def list_nodes(project_api_id: str) -> List[NodeResponse]:
    await get_project_document(project_api_id)
    nodes = await get_nodes_by_project_document(project_api_id)
    return [
        NodeResponse.model_validate(
            serialize_document(_normalize_node_document(node))
        )
        for node in nodes
    ]


async def update_node(node_id: str, node: NodeCreate) -> NodeResponse:
    existing = await get_node_document_by_id(node_id)
    await get_project_document(node.project_api_id)
    if node.is_root:
        await nodes_collection.update_many(
            {
                "project_api_id": node.project_api_id,
                "is_root": True,
                "node_id": {"$ne": node_id},
            },
            {"$set": {"is_root": False, "updated_at": utc_now()}},
        )
    http_config = (
        node.http_config.model_dump(by_alias=True) if node.http_config else None
    )
    await nodes_collection.update_one(
        {"node_id": node_id},
        {
            "$set": {
                "project_api_id": node.project_api_id,
                "name": node.name,
                "description": node.description,
                "node_schema": node.node_schema.model_dump(by_alias=True),
                "runtime": node.runtime,
                "tool_code": node.tool_code or "",
                "http_config": http_config,
                "is_root": node.is_root,
                "dependencies": node.dependencies,
                "updated_at": utc_now(),
            }
        },
    )
    updated = {
        **existing,
        "project_api_id": node.project_api_id,
        "name": node.name,
        "description": node.description,
        "node_schema": node.node_schema.model_dump(by_alias=True),
        "runtime": node.runtime,
        "tool_code": node.tool_code or "",
        "http_config": http_config,
        "is_root": node.is_root,
        "dependencies": node.dependencies,
        "updated_at": utc_now(),
    }
    await _create_node_version(updated, source="update")
    return await get_node(node_id)


async def delete_node(node_id: str) -> MessageResponse:
    result = await nodes_collection.delete_one({"node_id": node_id})
    if result.deleted_count == 0:
        raise NotFoundError(f"Node {node_id} not found", code="node_not_found")
    return MessageResponse(message="Node deleted successfully")


def _normalize_execution_result(
    result: Dict[str, Any], cache_hit: bool
) -> LiteralStatus:
    if cache_hit:
        return "cached"
    if result.get("success") is True:
        return "success"
    if str(result.get("error", "")).lower().find("timeout") >= 0:
        return "timeout"
    return "error"


LiteralStatus = str


def _build_cache_key(
    project_api_id: str,
    node_name: str,
    tool_code: str,
    input_data: Dict[str, Any],
    *,
    runtime: str = "javascript",
    http_config: Optional[Dict[str, Any]] = None,
) -> str:
    payload = json.dumps(
        {
            "project_api_id": project_api_id,
            "node_name": node_name,
            "runtime": runtime,
            "tool_code": tool_code,
            "http_config": http_config,
            "input_data": input_data,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _get_cached_response(cache_key: str) -> Optional[NodeExecutionResponse]:
    base = await _orchestration.get_cached_execution(cache_key)
    if not base:
        return None
    metadata = ExecutionMetadata(
        execution_id=f"exec_{ObjectId()}",
        status="cached",
        worker_id=base.metadata.worker_id,
        cache_hit=True,
        attempts=1,
        duration_ms=0.0,
        timeout_ms=base.metadata.timeout_ms,
        started_at=utc_now(),
        completed_at=utc_now(),
        cache_key=cache_key,
    )
    return NodeExecutionResponse(
        node_id=base.node_id,
        node_name=base.node_name,
        result=base.result,
        executed_at=utc_now(),
        metadata=metadata,
    )


async def _cache_response(
    cache_key: str, response: NodeExecutionResponse, ttl_seconds: int
) -> None:
    if ttl_seconds <= 0 or response.metadata.status not in {"success"}:
        return
    await _orchestration.set_cached_execution(cache_key, response, ttl_seconds)


def _should_retry(result: Dict[str, Any]) -> bool:
    if result.get("success") is True:
        return False
    error_text = str(result.get("error", "")).lower()
    transient_markers = [
        "timeout",
        "timed out",
        "econnreset",
        "socket",
        "fetch failed",
        "network",
        "503",
        "502",
    ]
    return any(marker in error_text for marker in transient_markers)


async def _persist_execution_history(
    *,
    project_api_id: str,
    node_id: str,
    node_name: str,
    source: str,
    input_data: Dict[str, Any],
    result: Any,
    metadata: ExecutionMetadata,
    conversation_id: Optional[str] = None,
    test_run_id: Optional[str] = None,
) -> None:
    await node_executions_collection.insert_one(
        {
            "execution_id": metadata.execution_id,
            "project_api_id": project_api_id,
            "node_id": node_id,
            "node_name": node_name,
            "source": source,
            "conversation_id": conversation_id,
            "test_run_id": test_run_id,
            "input_data": input_data,
            "result": result,
            "metadata": metadata.model_dump(mode="json"),
            "created_at": metadata.completed_at,
        }
    )


async def list_execution_history(
    project_api_id: str, limit: int = 50
) -> List[ExecutionHistoryResponse]:
    await get_project_document(project_api_id)
    history = (
        await node_executions_collection.find({"project_api_id": project_api_id})
        .sort("created_at", -1)
        .to_list(length=limit)
    )
    return [
        ExecutionHistoryResponse.model_validate(serialize_document(item))
        for item in history
    ]


async def execute_named_node(
    project_api_id: str,
    node_name: str,
    input_data: Dict[str, Any],
    *,
    source: str = "api",
    conversation_id: Optional[str] = None,
    test_run_id: Optional[str] = None,
    timeout_ms: Optional[int] = None,
    cache_ttl_seconds: Optional[int] = None,
    max_retries: Optional[int] = None,
) -> NodeExecutionResponse:
    await get_project_document(project_api_id)
    node = await nodes_collection.find_one(
        {"project_api_id": project_api_id, "name": node_name}
    )
    if not node:
        raise NotFoundError(f"Node '{node_name}' not found", code="node_not_found")

    timeout_ms = timeout_ms or Config.NODE_EXECUTION_TIMEOUT_MS
    cache_ttl_seconds = (
        Config.NODE_EXECUTION_CACHE_TTL_SECONDS
        if cache_ttl_seconds is None
        else cache_ttl_seconds
    )
    max_retries = (
        Config.NODE_EXECUTION_MAX_RETRIES if max_retries is None else max_retries
    )

    required_inputs = set(node.get("node_schema", {}).get("inputs", {}).keys())
    missing_inputs = sorted(required_inputs - set(input_data.keys()))
    if missing_inputs:
        logger.warning(
            "Node executed with missing inputs",
            extra={
                "event": "node_missing_inputs",
                "project_api_id": project_api_id,
                "node_name": node_name,
            },
        )

    runtime = (node.get("runtime") or "javascript").lower()
    http_config = node.get("http_config")
    tool_code = node.get("tool_code") or ""

    cache_key = _build_cache_key(
        project_api_id,
        node_name,
        tool_code,
        input_data,
        runtime=runtime,
        http_config=http_config,
    )
    cached_response = await _get_cached_response(cache_key)
    if cached_response:
        await _persist_execution_history(
            project_api_id=project_api_id,
            node_id=node["node_id"],
            node_name=node_name,
            source=source,
            input_data=input_data,
            result=cached_response.result,
            metadata=cached_response.metadata,
            conversation_id=conversation_id,
            test_run_id=test_run_id,
        )
        return cached_response

    started_at = utc_now()
    retry_reason = None
    last_result: Dict[str, Any] = {"error": "Execution did not start", "success": False}
    worker_id: Optional[str] = None
    attempts = 0

    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            worker_result = await asyncio.wait_for(
                _runtime_registry.execute(
                    runtime,
                    code=tool_code,
                    input_data=input_data,
                    http_config=http_config,
                    timeout_ms=timeout_ms,
                ),
                timeout=timeout_ms / 1000 + 5,
            )
            worker_id = worker_result.get("worker_id")
            logs = worker_result.get("logs") or []
            if worker_result.get("ok"):
                result_payload = worker_result.get("result")
                if isinstance(result_payload, dict):
                    last_result = {
                        **result_payload,
                        "success": result_payload.get("success", True),
                        "logs": logs,
                    }
                else:
                    last_result = {
                        "output": result_payload,
                        "success": True,
                        "logs": logs,
                    }
            else:
                error_message = (
                    worker_result.get("error", {}) or {}
                ).get("message", "Node runtime execution failed")
                if worker_result.get("result") and isinstance(
                    worker_result.get("result"), dict
                ):
                    last_result = {
                        **worker_result["result"],
                        "success": False,
                        "error": error_message,
                        "logs": logs,
                    }
                else:
                    last_result = {
                        "error": error_message,
                        "success": False,
                        "logs": logs,
                    }
        except asyncio.TimeoutError:
            last_result = {"error": "Execution timeout", "success": False}
        except Exception as exc:
            retry_reason = str(exc)
            last_result = {"error": str(exc), "success": False}

        if (
            last_result.get("success") is True
            or attempt >= max_retries
            or not _should_retry(last_result)
        ):
            break
        retry_reason = last_result.get("error")

    completed_at = utc_now()
    metadata = ExecutionMetadata(
        execution_id=f"exec_{ObjectId()}",
        status=_normalize_execution_result(last_result, cache_hit=False),
        worker_id=worker_id,
        cache_hit=False,
        attempts=attempts,
        duration_ms=round((completed_at - started_at).total_seconds() * 1000, 2),
        timeout_ms=timeout_ms,
        started_at=started_at,
        completed_at=completed_at,
        cache_key=cache_key,
        retry_reason=retry_reason,
    )
    response = NodeExecutionResponse(
        node_id=node["node_id"],
        node_name=node_name,
        result=last_result,
        executed_at=completed_at,
        metadata=metadata,
    )

    await _cache_response(cache_key, response, cache_ttl_seconds)
    await _persist_execution_history(
        project_api_id=project_api_id,
        node_id=node["node_id"],
        node_name=node_name,
        source=source,
        input_data=input_data,
        result=last_result,
        metadata=metadata,
        conversation_id=conversation_id,
        test_run_id=test_run_id,
    )
    return response


async def create_conversation(
    project_api_id: str,
    *,
    session_id: Optional[str] = None,
    is_test: bool = False,
    reset_from: Optional[str] = None,
    preserved_context: Optional[Dict[str, Any]] = None,
    initial_messages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    document = {
        "conversation_id": f"{'test_conv' if is_test else 'conv'}_{ObjectId()}",
        "project_api_id": project_api_id,
        "session_id": session_id,
        "messages": initial_messages or [],
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "is_test": is_test,
    }
    if reset_from:
        document["reset_from"] = reset_from
    if preserved_context:
        document["preserved_context"] = preserved_context
    result = await conversations_collection.insert_one(document)
    document["_id"] = result.inserted_id
    return document


async def get_conversation_document(conversation_id: str) -> Dict[str, Any]:
    conversation = await conversations_collection.find_one(
        {"conversation_id": conversation_id}
    )
    if not conversation:
        raise NotFoundError("Conversation not found", code="conversation_not_found")
    return conversation


async def get_conversation(conversation_id: str) -> ConversationResponse:
    return ConversationResponse.model_validate(
        serialize_document(await get_conversation_document(conversation_id))
    )


async def persist_conversation_messages(
    conversation_id: str, messages: List[Dict[str, Any]], **extra_fields: Any
) -> None:
    await conversations_collection.update_one(
        {"conversation_id": conversation_id},
        {"$set": {"messages": messages, "updated_at": utc_now(), **extra_fields}},
    )


_WEAK_ANSWER_MARKERS = (
    "i'm here to help",
    "i am here to help",
    "could you please provide more details",
    "provide more details about what you're looking for",
    "how can i help you today",
    "tool execution complete",
    "summarize tool result",
    "let me search for that",
    "processing request",
)


def _is_weak_bot_answer(text: Optional[str]) -> bool:
    cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not cleaned:
        return True
    if len(cleaned) < 48:
        return True
    return any(marker in cleaned for marker in _WEAK_ANSWER_MARKERS)


def _extract_result_payload(node_result: Any) -> Any:
    if not isinstance(node_result, dict):
        return node_result
    if "body" in node_result:
        return node_result.get("body")
    if "data" in node_result:
        return node_result.get("data")
    if "output" in node_result and len(node_result) <= 4:
        return node_result.get("output")
    return node_result


def _compact_tool_result_for_llm(
    node_name: str, node_result: Any, *, max_items: int = 25, max_chars: int = 3500
) -> str:
    """Shrink huge tool payloads so small local models can still summarize."""
    payload = _extract_result_payload(node_result)
    if isinstance(payload, list):
        sample = payload[:max_items]
        if sample and isinstance(sample[0], dict) and "name" in sample[0]:
            lines = []
            for item in sample:
                level = item.get("level", "")
                name = str(item.get("name", "")).strip()
                slug = str(item.get("slug", "")).strip()
                lines.append(f"- {name} (slug={slug}, level={level})")
            more = len(payload) - len(sample)
            text = (
                f"Tool {node_name} returned {len(payload)} categories/items:\n"
                + "\n".join(lines)
            )
            if more > 0:
                text += f"\n... and {more} more."
            return text[:max_chars]
        text = json.dumps(sample, default=str, ensure_ascii=False)
        more = len(payload) - len(sample)
        if more > 0:
            text += f"\n(... {more} more items)"
        return f"Tool {node_name} returned a list of {len(payload)} items:\n{text}"[
            :max_chars
        ]

    text = json.dumps(payload, default=str, ensure_ascii=False)
    return f"Tool {node_name} result:\n{text}"[:max_chars]


def ground_tool_result_answer(
    node_name: str,
    node_result: Any,
    user_message: str,
) -> str:
    """
    Deterministic, grounded user-facing answer from tool output.
    Used when LLM second-pass is weak (keyword fallback / small local models).
    """
    if isinstance(node_result, dict) and node_result.get("success") is False:
        err = node_result.get("error") or node_result.get("message") or "unknown error"
        return f"I ran **{node_name}**, but it failed: {err}"

    payload = _extract_result_payload(node_result)
    user_lower = (user_message or "").lower()
    stop = {
        "list",
        "me",
        "the",
        "a",
        "an",
        "about",
        "or",
        "any",
        "all",
        "show",
        "get",
        "please",
        "categories",
        "category",
        "docs",
        "documents",
        "what",
        "are",
        "there",
        "of",
        "to",
        "for",
        "with",
        "and",
    }
    terms = [
        t
        for t in re.findall(r"[a-z0-9']+", user_lower)
        if len(t) > 2 and t not in stop
    ]

    if isinstance(payload, list):
        items = [item for item in payload if isinstance(item, dict)]
        if items and any("name" in item for item in items):
            filtered = items
            if terms:
                matched = [
                    item
                    for item in items
                    if any(
                        term
                        in f"{item.get('name', '')} {item.get('slug', '')}".lower()
                        for term in terms
                    )
                ]
                if matched:
                    filtered = matched

            top_level = [item for item in filtered if item.get("level") in (None, 1)]
            focus = top_level if top_level else filtered
            # Prefer compact top-level overview for category trees
            if "aidoc_categories" in node_name or any(
                "slug" in item for item in items
            ):
                names = [
                    str(item.get("name", "")).strip()
                    for item in focus
                    if str(item.get("name", "")).strip()
                ]
                names = names[:50]
                total = len(items)
                matched_n = len(filtered)
                header = (
                    f"Here are the document categories"
                    + (
                        f" matching **{', '.join(terms)}**"
                        if terms and matched_n < total
                        else ""
                    )
                    + f" ({len(names)} shown"
                    + (f", {total} total" if total != len(names) else "")
                    + "):"
                )
                body = "\n".join(f"• {name}" for name in names)
                if not names:
                    return (
                        f"I loaded **{total}** categories from AI-DOC, but none matched "
                        f"**{', '.join(terms) or 'your filter'}**. Try a broader term."
                    )
                return f"{header}\n{body}"

            # Generic list of objects
            lines = []
            for item in filtered[:30]:
                if "name" in item:
                    lines.append(f"• {item.get('name')}")
                else:
                    lines.append(f"• {json.dumps(item, default=str)[:160]}")
            return (
                f"Result from **{node_name}** ({len(filtered)} items):\n"
                + "\n".join(lines)
            )

        # list of scalars
        preview = ", ".join(str(x) for x in payload[:40])
        return f"Result from **{node_name}**: {preview}"

    if isinstance(payload, dict):
        # semantic search style
        if "answer" in payload or "matches" in payload:
            answer = payload.get("answer")
            matches = payload.get("matches") or []
            parts = []
            if answer:
                parts.append(str(answer).strip())
            elif matches:
                parts.append(f"I found {len(matches)} relevant passage(s):")
                for match in matches[:5]:
                    if isinstance(match, dict):
                        text = str(match.get("text") or "")[:280]
                        filename = match.get("filename") or match.get("doc_id") or ""
                        page = match.get("page")
                        meta = f" ({filename}" + (f", p.{page}" if page else "") + ")"
                        parts.append(f"• {text}{meta if filename else ''}")
            if parts:
                return "\n".join(parts)

        if "status" in payload and (
            "ocr_engine" in payload or payload.get("status") in {"healthy", "degraded"}
        ):
            return (
                f"AI-DOC health: **{payload.get('status')}**"
                + (
                    f" · engine **{payload.get('ocr_engine')}**"
                    if payload.get("ocr_engine")
                    else ""
                )
            )

        if "total" in payload and (
            "by_category" in payload or "items" in payload
        ):
            total = payload.get("total")
            by_cat = payload.get("by_category") or {}
            lines = [f"There are **{total}** jobs/documents indexed."]
            if isinstance(by_cat, dict) and by_cat:
                top = sorted(
                    by_cat.items(),
                    key=lambda kv: int(kv[1]) if str(kv[1]).isdigit() else 0,
                    reverse=True,
                )[:15]
                lines.append("Top categories by count:")
                for key, count in top:
                    lines.append(f"• {key}: {count}")
            return "\n".join(lines)

        if "access_token" in payload or (
            isinstance(payload.get("user"), dict) and payload.get("role")
        ):
            role = payload.get("role") or (payload.get("user") or {}).get("role")
            email = (payload.get("user") or {}).get("email")
            return (
                f"Login successful"
                + (f" as **{email}**" if email else "")
                + (f" (role: {role})" if role else "")
                + ". You can now list jobs or run protected actions."
            )

        # short dict dump
        text = json.dumps(payload, default=str, ensure_ascii=False)
        if len(text) > 1200:
            text = text[:1200] + "…"
        return f"Result from **{node_name}**:\n```json\n{text}\n```"

    return f"Result from **{node_name}**: {payload}"


def _is_litellm_mode() -> bool:
    return (Config.LLM_PROVIDER or "").lower() in {"litellm", "auto", "database"}


def _pinned_provider_id(project: Dict[str, Any]) -> str:
    """DB-driven per-project pin (project.llm_config.provider_id), if any."""
    llm_config = project.get("llm_config") or {}
    if not isinstance(llm_config, dict):
        return ""
    return str(llm_config.get("provider_id") or "").strip()


async def _generate_via_litellm_chain(
    project: Dict[str, Any],
    user_message: str,
    nodes: List[Dict[str, Any]],
    conversation_history: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = None,
    stop_sequences: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Try ordered DB providers (default first), fall back to keyword router."""
    from app.services_llm_providers import (
        record_llm_usage,
        resolve_providers_for_project,
        to_runtime_config,
    )
    from llm.litellm_provider import LiteLLMProvider

    try:
        docs = await resolve_providers_for_project(project)
    except Exception:
        logger.exception(
            "Failed to resolve LLM providers; using keyword fallback",
            extra={"event": "llm_resolve_failed", "error_code": "llm_resolve_failed"},
        )
        docs = []

    llm_config = project.get("llm_config") or {}
    model_override = llm_config.get("model_override") if isinstance(llm_config, dict) else None
    last_error: Optional[str] = None
    for doc in docs:
        config = to_runtime_config(doc, model_override=model_override)
        if not config["api_key"] and str(doc.get("auth_type") or "bearer").lower() != "none":
            last_error = (
                f"Provider '{doc.get('name')}' has no API key "
                f"(set api_key or env {doc.get('api_key_env')})."
            )
            continue
        try:
            response = await LiteLLMProvider(config).generate(
                project=project,
                user_message=user_message,
                nodes=nodes,
                conversation_history=conversation_history,
                temperature=temperature if temperature is not None else float(
                    llm_config.get("temperature") if isinstance(llm_config, dict) else 0.4
                ) if isinstance(llm_config, dict) and llm_config.get("temperature") is not None else None,
                stop_sequences=stop_sequences,
            )
            response["debug_info"] = (
                (response.get("debug_info") or "")
                + f"\nprovider_id={doc.get('provider_id')}"
            ).strip()
            await record_llm_usage(str(doc.get("provider_id")), ok=True)
            global _llm_status
            _llm_status = f"ready:{doc.get('provider')}:{config['model']}"
            return response
        except Exception as exc:
            last_error = str(exc)[:1000]
            await record_llm_usage(str(doc.get("provider_id")), ok=False)
            logger.warning(
                "LiteLLM provider failed, trying next",
                extra={
                    "event": "llm_provider_attempt_failed",
                    "error_code": "llm_provider_attempt_failed",
                    "node_name": str(doc.get("name")),
                    "error": last_error,
                },
            )
            continue

    # All cloud providers unavailable -> deterministic lexical fallback (offline-safe).
    from llm.integration import KeywordMatchProvider

    fallback = await KeywordMatchProvider().generate(
        project=project,
        user_message=user_message,
        nodes=nodes,
        conversation_history=conversation_history,
    )
    if last_error:
        fallback["debug_info"] = (
            (fallback.get("debug_info") or "")
            + f"\ncloud_unavailable: {last_error}"
        ).strip()
    return fallback


async def generate_llm_response(
    project: Dict[str, Any],
    user_message: str,
    nodes: List[Dict[str, Any]],
    conversation_history: List[Dict[str, Any]],
    *,
    temperature: Optional[float] = None,
    stop_sequences: Optional[List[str]] = None,
    disable_tools: bool = False,
) -> Dict[str, Any]:
    effective_nodes = nodes if not disable_tools else []
    # DB-driven pin always wins: a project bound to a provider uses ONLY that
    # provider regardless of the global LLM_PROVIDER mode. Otherwise the global
    # mode decides (litellm chain vs legacy singleton).
    if _pinned_provider_id(project) or _is_litellm_mode():
        try:
            response = await _generate_via_litellm_chain(
                project,
                user_message,
                effective_nodes,
                conversation_history,
                temperature=temperature,
                stop_sequences=stop_sequences,
            )
        except Exception as exc:
            logger.exception(
                "LiteLLM chain failed",
                extra={
                    "event": "llm_generation_failed",
                    "error_code": "llm_generation_failed",
                },
            )
            response = {
                "action": "chat",
                "text_response": f"I apologize, I encountered an error. Please try again. Error: {exc}",
            }
        if disable_tools and response.get("action") == "call_node":
            response["action"] = "chat"
        return response
    provider = get_llm_provider()
    try:
        response = await provider.generate(
            project=project,
            user_message=user_message,
            nodes=nodes if not disable_tools else [],
            conversation_history=conversation_history,
            temperature=temperature,
            stop_sequences=stop_sequences,
        )
    except Exception as exc:
        logger.exception(
            "LLM generation failed",
            extra={
                "event": "llm_generation_failed",
                "error_code": "llm_generation_failed",
            },
        )
        return {
            "action": "chat",
            "text_response": f"I apologize, I encountered an error. Please try again. Error: {exc}",
        }
    if disable_tools and response.get("action") == "call_node":
        response["action"] = "chat"
    return response


async def build_chat_response(chat_request: ChatRequest) -> ChatResponse:
    project = await get_project_document(chat_request.project_api_id)
    nodes = await get_nodes_by_project_document(chat_request.project_api_id)
    conversation: Optional[Dict[str, Any]] = None
    recovery_used = False

    if chat_request.conversation_id and not chat_request.force_new:
        conversation = await conversations_collection.find_one(
            {"conversation_id": chat_request.conversation_id}
        )
        recovery_used = conversation is None
    if not conversation and not chat_request.force_new:
        query: Dict[str, Any] = {"project_api_id": chat_request.project_api_id}
        if chat_request.session_id:
            query["session_id"] = chat_request.session_id
        latest = (
            await conversations_collection.find(query)
            .sort("created_at", -1)
            .limit(1)
            .to_list(length=1)
        )
        if latest:
            conversation = latest[0]
            recovery_used = True
    if not conversation:
        conversation = await create_conversation(
            chat_request.project_api_id, session_id=chat_request.session_id
        )
    elif chat_request.session_id and not conversation.get("session_id"):
        await conversations_collection.update_one(
            {"_id": conversation["_id"]},
            {"$set": {"session_id": chat_request.session_id, "updated_at": utc_now()}},
        )
        conversation["session_id"] = chat_request.session_id

    conversation.setdefault("messages", [])
    user_message = {
        "role": "user",
        "content": chat_request.user_message,
        "timestamp": utc_now(),
    }
    conversation["messages"].append(user_message)

    conversation_reset = False
    if len(conversation["messages"]) > Config.CONVERSATION_MESSAGE_LIMIT:
        user_email = None
        is_authenticated = False
        recent_context = ""
        for message in reversed(conversation["messages"][-30:]):
            content = str(message.get("content", ""))
            if "TOOL RESULT for check_user_existence" in content:
                email_match = re.search(
                    r'email["\']?\s*[:=]\s*["\']([^"\']+)["\']', content
                )
                if email_match:
                    user_email = email_match.group(1)
                    is_authenticated = True
                break
        recent_user_messages = [
            str(message.get("content", ""))
            for message in conversation["messages"][-10:]
            if message.get("role") == "user"
        ]
        if recent_user_messages:
            recent_context = (
                f"User was recently: {', '.join(recent_user_messages[-3:])}"
            )
        preserved_context = {
            "user_email": user_email,
            "is_authenticated": is_authenticated,
            "recent_context": recent_context,
        }
        initial_messages: List[Dict[str, Any]] = []
        if user_email:
            content = (
                f"[System: Conversation optimized. User {user_email} is authenticated.]"
            )
            if recent_context:
                content += f" {recent_context}"
            initial_messages.append(
                {"role": "system", "content": content, "timestamp": utc_now()}
            )
        initial_messages.append(user_message)
        conversation = await create_conversation(
            chat_request.project_api_id,
            session_id=chat_request.session_id,
            reset_from=conversation["conversation_id"],
            preserved_context=preserved_context,
            initial_messages=initial_messages,
        )
        conversation_reset = True

    llm_decision = await generate_llm_response(
        project, chat_request.user_message, nodes, conversation["messages"]
    )
    bot_response = ""
    node_executed = None
    node_result = None
    execution_metadata = None
    fallback_used = False
    selected_node = None

    if llm_decision.get("action") == "call_node":
        selected_node = next(
            (
                node
                for node in nodes
                if node.get("name") == llm_decision.get("node_name")
            ),
            None,
        )
    else:
        fallback = _find_root_fallback_node(nodes, chat_request.user_message)
        if fallback:
            fallback_node, fallback_inputs = fallback
            llm_decision = {
                **llm_decision,
                "action": "call_node",
                "node_name": fallback_node["name"],
                "inputs": fallback_inputs,
                "text_response": llm_decision.get("text_response")
                or f"I am routing this through the root workflow {fallback_node['name']}.",
            }
            selected_node = fallback_node
            fallback_used = True

    if llm_decision.get("action") == "call_node":
        node_name = llm_decision.get("node_name")
        inputs = llm_decision.get("inputs", {})
        if not node_name:
            raise InternalServerError(
                "LLM returned a tool call without a node name", code="invalid_tool_call"
            )
        execution = await execute_named_node(
            chat_request.project_api_id,
            node_name,
            inputs,
            source="chat",
            conversation_id=conversation["conversation_id"],
        )
        node_executed = node_name
        node_result = execution.result
        execution_metadata = execution.metadata

        # Always available grounded answer (truth from tool, not LLM)
        grounded = ground_tool_result_answer(
            node_name, node_result, chat_request.user_message
        )
        compact_for_llm = _compact_tool_result_for_llm(node_name, node_result)

        conversation["messages"].append(
            {
                "role": "system",
                "content": (
                    f"TOOL RESULT for {node_name}:\n{compact_for_llm}\n\n"
                    "Write a natural, concise answer for the user using ONLY this tool result. "
                    "If the result is a list of categories, list the relevant names clearly. "
                    "Do not call another tool. Do not invent data. "
                    "Do not say you need more details if the tool already returned data."
                ),
                "timestamp": utc_now(),
            }
        )

        # Skip weak second-pass providers (keyword) — use grounded answer directly
        use_grounded_only = Config.LLM_PROVIDER == "keyword" or (
            get_llm_status() or ""
        ).startswith("degraded")

        if use_grounded_only:
            bot_response = grounded
        else:
            second_pass = await generate_llm_response(
                project,
                (
                    f"User asked: {chat_request.user_message}\n"
                    "Summarize the TOOL RESULT above into a clear helpful answer."
                ),
                nodes,
                conversation["messages"],
                temperature=0.3,
                stop_sequences=["User:", "Assistant:", "TOOL RESULT"],
                disable_tools=True,
            )
            bot_response = second_pass.get("text_response") or grounded
            if _is_weak_bot_answer(bot_response):
                logger.info(
                    "Second-pass LLM answer was weak; using grounded tool summary",
                    extra={
                        "event": "grounded_tool_answer_used",
                        "node_name": node_name,
                    },
                )
                bot_response = grounded
            if "debug_info" in second_pass:
                llm_decision["debug_info"] = (
                    (llm_decision.get("debug_info") or "")
                    + "\n\n--- SECOND PASS ---\n"
                    + second_pass["debug_info"]
                ).strip()
    else:
        bot_response = llm_decision.get("text_response", "")

    await _create_tool_audit(
        project_api_id=chat_request.project_api_id,
        user_message=chat_request.user_message,
        llm_action=llm_decision.get("action", "chat"),
        node=selected_node,
        conversation_id=conversation["conversation_id"],
        fallback_used=fallback_used,
        recovery_used=recovery_used,
        reason=llm_decision.get("text_response"),
    )

    conversation["messages"].append(
        {
            "role": "assistant",
            "content": bot_response,
            "node_executed": node_executed,
            "node_result": node_result,
            "execution_metadata": execution_metadata.model_dump(mode="json")
            if execution_metadata
            else None,
            "timestamp": utc_now(),
        }
    )
    await persist_conversation_messages(
        conversation["conversation_id"], conversation["messages"]
    )
    return ChatResponse(
        conversation_id=conversation["conversation_id"],
        bot_response=bot_response,
        node_executed=node_executed,
        node_result=node_result,
        execution_metadata=execution_metadata,
        debug_info=llm_decision.get("debug_info"),
        conversation_reset=conversation_reset,
        preserved_context=conversation.get("preserved_context")
        if conversation_reset
        else None,
    )


async def run_project_tests_sync(
    project_api_id: str,
    test_request: ProjectTestRequest,
    *,
    test_run_id: Optional[str] = None,
) -> ProjectTestResponse:
    started_at = utc_now()
    project = await get_project_document(project_api_id)
    nodes = await get_nodes_by_project_document(project_api_id)
    conversation = await create_conversation(project_api_id, is_test=True)
    test_results: List[ProjectTestResult] = []
    successful_tests = 0
    failed_tests = 0

    for index, test_message in enumerate(test_request.test_messages, start=1):
        try:
            conversation["messages"].append(
                {"role": "user", "content": test_message, "timestamp": utc_now()}
            )
            llm_decision = await generate_llm_response(
                project, test_message, nodes, conversation["messages"]
            )
            bot_response = ""
            node_executed = None
            node_result = None
            execution_metadata = None
            execution_success = True
            error_message = None
            fallback_used = False
            selected_node = None

            if llm_decision.get("action") == "call_node":
                selected_node = next(
                    (
                        node
                        for node in nodes
                        if node.get("name") == llm_decision.get("node_name")
                    ),
                    None,
                )
            else:
                fallback = _find_root_fallback_node(nodes, test_message)
                if fallback:
                    fallback_node, fallback_inputs = fallback
                    llm_decision = {
                        **llm_decision,
                        "action": "call_node",
                        "node_name": fallback_node["name"],
                        "inputs": fallback_inputs,
                        "text_response": llm_decision.get("text_response")
                        or f"Running root workflow {fallback_node['name']} for simulation.",
                    }
                    fallback_used = True
                    selected_node = fallback_node

            if llm_decision.get("action") == "call_node":
                node_name = llm_decision.get("node_name")
                inputs = llm_decision.get("inputs", {})
                if not node_name:
                    raise InternalServerError(
                        "LLM returned a tool call without a node name",
                        code="invalid_tool_call",
                    )
                execution = await execute_named_node(
                    project_api_id,
                    node_name,
                    inputs,
                    source="test",
                    conversation_id=conversation["conversation_id"],
                    test_run_id=test_run_id,
                )
                node_executed = node_name
                node_result = execution.result
                execution_metadata = execution.metadata
                execution_success = execution.result.get("success", False)
                bot_response = f"{llm_decision.get('text_response', '')}\n\nResult: {json.dumps(node_result, indent=2)}"
                if not execution_success:
                    error_message = str(
                        node_result.get("error", "Node execution failed")
                    )
            else:
                bot_response = llm_decision.get("text_response", "")

            conversation["messages"].append(
                {
                    "role": "assistant",
                    "content": bot_response,
                    "node_executed": node_executed,
                    "node_result": node_result,
                    "execution_metadata": execution_metadata.model_dump(mode="json")
                    if execution_metadata
                    else None,
                    "timestamp": utc_now(),
                }
            )

            await _create_tool_audit(
                project_api_id=project_api_id,
                user_message=test_message,
                llm_action=llm_decision.get("action", "chat"),
                node=selected_node,
                conversation_id=conversation["conversation_id"],
                fallback_used=fallback_used,
                recovery_used=False,
                reason=llm_decision.get("text_response"),
            )

            test_results.append(
                ProjectTestResult(
                    test_number=index,
                    user_message=test_message,
                    bot_response=bot_response,
                    action=llm_decision.get("action"),
                    node_executed=node_executed,
                    node_result=node_result
                    if test_request.show_node_execution and node_result is not None
                    else None,
                    execution_metadata=execution_metadata,
                    success=execution_success,
                    error=error_message,
                )
            )
            if execution_success:
                successful_tests += 1
            else:
                failed_tests += 1
        except Exception as exc:
            test_results.append(
                ProjectTestResult(
                    test_number=index,
                    user_message=test_message,
                    success=False,
                    error=str(exc),
                )
            )
            failed_tests += 1

    completed_at = utc_now()
    await persist_conversation_messages(
        conversation["conversation_id"], conversation["messages"]
    )
    return ProjectTestResponse(
        project_api_id=project_api_id,
        project_name=project["name"],
        total_tests=len(test_request.test_messages),
        successful_tests=successful_tests,
        failed_tests=failed_tests,
        test_results=test_results,
        conversation_id=conversation["conversation_id"],
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=round((completed_at - started_at).total_seconds() * 1000, 2),
    )


async def queue_project_test_run(
    project_api_id: str, test_request: ProjectTestRequest
) -> ProjectTestQueuedResponse:
    project = await get_project_document(project_api_id)
    queued_at = utc_now()
    test_run_id = f"testrun_{ObjectId()}"
    await test_runs_collection.insert_one(
        {
            "test_run_id": test_run_id,
            "status": "queued",
            "project_api_id": project_api_id,
            "project_name": project["name"],
            "total_tests": len(test_request.test_messages),
            "successful_tests": 0,
            "failed_tests": 0,
            "queued_at": queued_at,
            "request": test_request.model_dump(mode="json"),
        }
    )
    await _orchestration.enqueue_test_run(test_run_id, project_api_id, test_request)
    return ProjectTestQueuedResponse(
        status="queued",
        test_run_id=test_run_id,
        project_api_id=project_api_id,
        project_name=project["name"],
        total_tests=len(test_request.test_messages),
        queued_at=queued_at,
        poll_url=f"/api/test-runs/{test_run_id}",
    )


async def run_project_tests(
    project_api_id: str, test_request: ProjectTestRequest
) -> ProjectTestRouteResponse:
    should_queue = test_request.execution_mode == "async" or (
        test_request.execution_mode == "auto"
        and len(test_request.test_messages) >= Config.TEST_QUEUE_ASYNC_THRESHOLD
    )
    if should_queue:
        return await queue_project_test_run(project_api_id, test_request)
    return await run_project_tests_sync(project_api_id, test_request)


async def get_test_run(test_run_id: str) -> ProjectTestRunResponse:
    test_run = await test_runs_collection.find_one({"test_run_id": test_run_id})
    if not test_run:
        raise NotFoundError("Test run not found", code="test_run_not_found")
    return ProjectTestRunResponse.model_validate(serialize_document(test_run))


async def get_stats() -> StatsResponse:
    project_count = await projects_collection.count_documents({})
    node_count = await nodes_collection.count_documents({})
    conversations = await conversations_collection.find({}).to_list(length=5000)
    messages_processed = sum(
        len(conversation.get("messages", [])) for conversation in conversations
    )

    total_executions = await node_executions_collection.count_documents({})
    successful_executions = await node_executions_collection.count_documents(
        {"metadata.status": {"$in": ["success", "cached"]}}
    )
    failed_executions = await node_executions_collection.count_documents(
        {"metadata.status": {"$in": ["error", "timeout"]}}
    )
    cached_executions = await node_executions_collection.count_documents(
        {"metadata.status": "cached"}
    )
    queued_test_runs = await test_runs_collection.count_documents({"status": "queued"})
    running_test_runs = await test_runs_collection.count_documents(
        {"status": "running"}
    )

    latency_pipeline = [
        {"$group": {"_id": None, "average": {"$avg": "$metadata.duration_ms"}}},
    ]
    latency_result = await node_executions_collection.aggregate(
        latency_pipeline
    ).to_list(length=1)
    average_latency_ms = (
        round(float(latency_result[0]["average"]), 2)
        if latency_result and latency_result[0].get("average") is not None
        else 0.0
    )
    success_rate = (
        round((successful_executions / total_executions) * 100, 2)
        if total_executions
        else 100.0
    )

    return StatsResponse(
        total_projects=project_count,
        active_nodes=node_count,
        messages_processed=messages_processed,
        total_executions=total_executions,
        successful_executions=successful_executions,
        failed_executions=failed_executions,
        cached_executions=cached_executions,
        average_latency_ms=average_latency_ms,
        success_rate=success_rate,
        queued_test_runs=queued_test_runs,
        running_test_runs=running_test_runs,
        timestamp=utc_now(),
    )


async def list_project_templates() -> List[ProjectTemplateResponse]:
    templates = (
        await project_templates_collection.find({}).sort("name", 1).to_list(length=200)
    )
    response_items: List[ProjectTemplateResponse] = []
    for item in templates:
        serialized = serialize_document(item)
        serialized.pop("_id", None)
        response_items.append(ProjectTemplateResponse.model_validate(serialized))
    return response_items


async def create_project_template(
    template: ProjectTemplateCreate,
) -> ProjectTemplateResponse:
    payload = {
        "template_id": f"tpl_{ObjectId()}",
        "name": template.name,
        "category": template.category,
        "description": template.description,
        "llm_prompt": template.llm_prompt,
        "suggested_nodes": template.suggested_nodes,
        "suggested_test_messages": template.suggested_test_messages,
    }
    await project_templates_collection.insert_one(payload)
    return ProjectTemplateResponse.model_validate(serialize_document(payload))


async def update_project_template(
    template_id: str, template: ProjectTemplateUpdate
) -> ProjectTemplateResponse:
    result = await project_templates_collection.update_one(
        {"template_id": template_id},
        {
            "$set": {
                "name": template.name,
                "category": template.category,
                "description": template.description,
                "llm_prompt": template.llm_prompt,
                "suggested_nodes": template.suggested_nodes,
                "suggested_test_messages": template.suggested_test_messages,
            }
        },
    )
    if result.matched_count == 0:
        raise NotFoundError("Template not found", code="template_not_found")
    updated = await _get_template_document(template_id)
    serialized = serialize_document(updated)
    serialized.pop("_id", None)
    return ProjectTemplateResponse.model_validate(serialized)


async def list_prompt_presets(project_api_id: str) -> List[PromptPresetResponse]:
    await get_project_document(project_api_id)
    presets = (
        await prompt_presets_collection.find({"project_api_id": project_api_id})
        .sort("created_at", -1)
        .to_list(length=100)
    )
    response_items: List[PromptPresetResponse] = []
    for item in presets:
        serialized = serialize_document(item)
        serialized.pop("_id", None)
        response_items.append(PromptPresetResponse.model_validate(serialized))
    return response_items


async def create_prompt_preset(
    project_api_id: str, preset: PromptPresetCreate
) -> PromptPresetResponse:
    await get_project_document(project_api_id)
    payload = {
        "preset_id": f"preset_{ObjectId()}",
        "project_api_id": project_api_id,
        "name": preset.name.strip(),
        "prompt": preset.prompt,
        "test_messages": preset.test_messages,
        "tags": preset.tags,
        "created_at": utc_now(),
    }
    await prompt_presets_collection.insert_one(payload)
    return PromptPresetResponse.model_validate(serialize_document(payload))


async def list_node_versions(node_id: str) -> List[NodeVersionResponse]:
    versions = (
        await node_versions_collection.find({"node_id": node_id})
        .sort("version_number", -1)
        .to_list(length=100)
    )
    return [
        NodeVersionResponse.model_validate(serialize_document(item))
        for item in versions
    ]


async def rollback_node_version(
    project_api_id: str, node_id: str, version_id: str
) -> NodeResponse:
    await get_project_document(project_api_id)
    target_version = await node_versions_collection.find_one(
        {
            "version_id": version_id,
            "node_id": node_id,
            "project_api_id": project_api_id,
        }
    )
    if not target_version:
        raise NotFoundError("Node version not found", code="node_version_not_found")
    await nodes_collection.update_one(
        {"node_id": node_id, "project_api_id": project_api_id},
        {
            "$set": {
                "name": target_version["name"],
                "description": target_version["description"],
                "node_schema": target_version["node_schema"],
                "runtime": target_version.get("runtime", "javascript"),
                "tool_code": target_version.get("tool_code") or "",
                "http_config": target_version.get("http_config"),
                "is_root": target_version.get("is_root", False),
                "dependencies": target_version.get("dependencies", []),
                "updated_at": utc_now(),
            }
        },
    )
    updated = await get_node_document_by_id(node_id)
    await _create_node_version(updated, source="manual")
    await write_governance_audit(
        "rollback_node_version",
        "node_version",
        version_id,
        details={"project_api_id": project_api_id, "node_id": node_id},
    )
    return NodeResponse.model_validate(
        serialize_document(_normalize_node_document(updated))
    )


async def get_project_graph(project_api_id: str) -> NodeGraphResponse:
    await get_project_document(project_api_id)
    nodes = await get_nodes_by_project_document(project_api_id)
    executions = await node_executions_collection.find(
        {"project_api_id": project_api_id}
    ).to_list(length=1000)

    execution_counts: Dict[str, int] = {}
    failure_counts: Dict[str, int] = {}
    observed_edges: Dict[tuple[str, str], int] = {}
    by_conversation: Dict[str, List[Dict[str, Any]]] = {}

    for execution in executions:
        node_name = execution.get("node_name")
        execution_counts[node_name] = execution_counts.get(node_name, 0) + 1
        status = execution.get("metadata", {}).get("status")
        if status in {"error", "timeout"}:
            failure_counts[node_name] = failure_counts.get(node_name, 0) + 1
        if execution.get("conversation_id"):
            by_conversation.setdefault(execution["conversation_id"], []).append(
                execution
            )

    for items in by_conversation.values():
        ordered = sorted(items, key=lambda item: item.get("created_at", ""))
        for previous, current in zip(ordered, ordered[1:]):
            key = (previous.get("node_name"), current.get("node_name"))
            observed_edges[key] = observed_edges.get(key, 0) + 1

    graph_nodes = [
        GraphNodeResponse(
            node_id=node["node_id"],
            name=node["name"],
            is_root=node.get("is_root", False),
            dependencies=node.get("dependencies", []),
            execution_count=execution_counts.get(node["name"], 0),
            failure_count=failure_counts.get(node["name"], 0),
        )
        for node in nodes
    ]

    name_to_id = {node["name"]: node["node_id"] for node in nodes}
    edges: List[GraphEdgeResponse] = []
    for node in nodes:
        for dependency in node.get("dependencies", []):
            if dependency in name_to_id:
                edges.append(
                    GraphEdgeResponse(
                        source=name_to_id[dependency],
                        target=node["node_id"],
                        type="declared",
                        weight=1,
                    )
                )
        if node.get("is_root"):
            for other in nodes:
                if other["node_id"] != node["node_id"] and not other.get(
                    "dependencies"
                ):
                    edges.append(
                        GraphEdgeResponse(
                            source=node["node_id"],
                            target=other["node_id"],
                            type="fallback",
                            weight=1,
                        )
                    )
    for (source_name, target_name), weight in observed_edges.items():
        if source_name in name_to_id and target_name in name_to_id:
            edges.append(
                GraphEdgeResponse(
                    source=name_to_id[source_name],
                    target=name_to_id[target_name],
                    type="observed",
                    weight=weight,
                )
            )

    return NodeGraphResponse(
        project_api_id=project_api_id, nodes=graph_nodes, edges=edges
    )


async def simulate_project_graph(project_api_id: str) -> SimulationResponse:
    await get_project_document(project_api_id)
    nodes = await get_nodes_by_project_document(project_api_id)
    issues: List[SimulationIssue] = []
    root_nodes = [node for node in nodes if node.get("is_root")]
    root_node_id = root_nodes[0]["node_id"] if root_nodes else None

    if not nodes:
        issues.append(
            SimulationIssue(
                level="error",
                code="no_nodes",
                message="Project does not contain any nodes",
            )
        )
    if not root_nodes:
        issues.append(
            SimulationIssue(
                level="warning",
                code="missing_root",
                message="No root node configured for fallback routing",
            )
        )
    if len(root_nodes) > 1:
        issues.append(
            SimulationIssue(
                level="error",
                code="multiple_roots",
                message="Multiple root nodes detected",
            )
        )

    node_names = {node["name"] for node in nodes}
    adjacency: Dict[str, List[str]] = {node["node_id"]: [] for node in nodes}
    name_to_id = {node["name"]: node["node_id"] for node in nodes}

    for node in nodes:
        dependencies = node.get("dependencies", [])
        if node.get("is_root") and dependencies:
            issues.append(
                SimulationIssue(
                    level="warning",
                    code="root_has_dependencies",
                    message="Root node should not depend on upstream nodes",
                    node_id=node["node_id"],
                )
            )
        for dependency in dependencies:
            if dependency not in node_names:
                issues.append(
                    SimulationIssue(
                        level="error",
                        code="missing_dependency",
                        message=f"Missing dependency '{dependency}'",
                        node_id=node["node_id"],
                    )
                )
            else:
                adjacency[name_to_id[dependency]].append(node["node_id"])
        if not node.get("tool_code", "").strip():
            issues.append(
                SimulationIssue(
                    level="error",
                    code="missing_tool_code",
                    message="Node has no executable tool code",
                    node_id=node["node_id"],
                )
            )

    visited: set[str] = set()
    stack: set[str] = set()

    def dfs(node_id: str) -> None:
        if node_id in stack:
            issues.append(
                SimulationIssue(
                    level="error",
                    code="cycle_detected",
                    message="Cycle detected in node dependency graph",
                    node_id=node_id,
                )
            )
            return
        if node_id in visited:
            return
        visited.add(node_id)
        stack.add(node_id)
        for child in adjacency.get(node_id, []):
            dfs(child)
        stack.remove(node_id)

    for node in nodes:
        dfs(node["node_id"])

    suggested_entrypoints = [
        node["name"]
        for node in nodes
        if node.get("is_root") or not node.get("dependencies")
    ]
    valid = not any(issue.level == "error" for issue in issues)
    return SimulationResponse(
        project_api_id=project_api_id,
        valid=valid,
        root_node_id=root_node_id,
        total_nodes=len(nodes),
        issues=issues,
        suggested_entrypoints=suggested_entrypoints,
    )


async def replay_execution(
    execution_id: str, request: ReplayExecutionRequest
) -> NodeExecutionResponse:
    execution = await node_executions_collection.find_one(
        {"execution_id": execution_id}
    )
    if not execution:
        raise NotFoundError("Execution not found", code="execution_not_found")
    return await execute_named_node(
        execution["project_api_id"],
        execution["node_name"],
        execution.get("input_data", {}),
        source="api",
        conversation_id=execution.get("conversation_id"),
        timeout_ms=request.timeout_ms,
        cache_ttl_seconds=request.cache_ttl_seconds,
        max_retries=request.max_retries,
    )


async def list_tool_audits(
    project_api_id: str, limit: int = 100
) -> List[ToolAuditResponse]:
    await get_project_document(project_api_id)
    audits = (
        await tool_audits_collection.find({"project_api_id": project_api_id})
        .sort("created_at", -1)
        .to_list(length=limit)
    )
    return [
        ToolAuditResponse.model_validate(serialize_document(item)) for item in audits
    ]


async def recover_conversation(
    project_api_id: str, session_id: Optional[str]
) -> ConversationRecoveryResponse:
    await get_project_document(project_api_id)
    query: Dict[str, Any] = {"project_api_id": project_api_id}
    if session_id:
        query["session_id"] = session_id
    conversation = await conversations_collection.find_one(
        query, sort=[("updated_at", -1), ("created_at", -1)]
    )
    if not conversation:
        return ConversationRecoveryResponse(
            project_api_id=project_api_id, session_id=session_id, recovered=False
        )

    latest_user = next(
        (
            message
            for message in reversed(conversation.get("messages", []))
            if message.get("role") == "user"
        ),
        None,
    )
    latest_node_message = next(
        (
            message
            for message in reversed(conversation.get("messages", []))
            if message.get("node_executed")
        ),
        None,
    )
    return ConversationRecoveryResponse(
        project_api_id=project_api_id,
        session_id=session_id,
        recovered=True,
        conversation_id=conversation.get("conversation_id"),
        latest_user_message=latest_user.get("content") if latest_user else None,
        latest_node=latest_node_message.get("node_executed")
        if latest_node_message
        else None,
        recommended_prompt=f"Resume the last known context for project {project_api_id} and continue naturally.",
        preserved_context=conversation.get("preserved_context"),
    )


async def get_control_plane_overview() -> ControlPlaneOverviewResponse:
    projects = await projects_collection.find({}).to_list(length=500)
    executions = await node_executions_collection.find({}).to_list(length=5000)
    conversations = await conversations_collection.find({}).to_list(length=5000)
    audits = await tool_audits_collection.find({}).to_list(length=5000)

    project_health: List[ProjectHealthMetric] = []
    for project in projects:
        project_executions = [
            item
            for item in executions
            if item.get("project_api_id") == project.get("project_api_id")
        ]
        total = len(project_executions)
        successes = sum(
            1
            for item in project_executions
            if item.get("metadata", {}).get("status") in {"success", "cached"}
        )
        failures = sum(
            1
            for item in project_executions
            if item.get("metadata", {}).get("status") in {"error", "timeout"}
        )
        avg_latency = (
            round(
                sum(
                    float(item.get("metadata", {}).get("duration_ms", 0))
                    for item in project_executions
                )
                / total,
                2,
            )
            if total
            else 0.0
        )
        success_rate = round((successes / total) * 100, 2) if total else 100.0
        health_score = round(
            max(
                0.0,
                min(
                    100.0,
                    success_rate
                    - min(30.0, failures * 2)
                    - min(20.0, avg_latency / 150),
                ),
            ),
            2,
        )
        last_activity = max(
            (item.get("created_at") for item in project_executions), default=None
        )
        project_health.append(
            ProjectHealthMetric(
                project_api_id=project["project_api_id"],
                project_name=project["name"],
                health_score=health_score,
                success_rate=success_rate,
                avg_latency_ms=avg_latency,
                failed_runs=failures,
                last_activity_at=last_activity,
            )
        )

    failed_node_counts: Dict[str, FailedNodeMetric] = {}
    for execution in executions:
        if execution.get("metadata", {}).get("status") in {"error", "timeout"}:
            node_name = execution.get("node_name")
            metric = failed_node_counts.get(node_name)
            latest_failure = execution.get("created_at")
            if metric is None:
                failed_node_counts[node_name] = FailedNodeMetric(
                    node_name=node_name, failures=1, latest_failure_at=latest_failure
                )
            else:
                metric.failures += 1
                if latest_failure and (
                    metric.latest_failure_at is None
                    or latest_failure > metric.latest_failure_at
                ):
                    metric.latest_failure_at = latest_failure

    total_conversations = len(conversations)
    recovered_conversations = sum(
        1
        for conversation in conversations
        if conversation.get("preserved_context") or conversation.get("reset_from")
    )
    avg_messages = (
        round(
            sum(len(conversation.get("messages", [])) for conversation in conversations)
            / total_conversations,
            2,
        )
        if total_conversations
        else 0.0
    )
    fallback_rate = (
        round(
            (sum(1 for audit in audits if audit.get("fallback_used")) / len(audits))
            * 100,
            2,
        )
        if audits
        else 0.0
    )
    llm_average_latency_ms = (
        round(
            sum(
                float(item.get("metadata", {}).get("duration_ms", 0))
                for item in executions
            )
            / len(executions),
            2,
        )
        if executions
        else 0.0
    )
    llm_cost_estimate_usd = round(
        sum(
            max(0.0002, len(json.dumps(item.get("result", {}))) / 50000)
            for item in executions
        ),
        4,
    )

    return ControlPlaneOverviewResponse(
        project_health=sorted(project_health, key=lambda item: item.health_score)[:20],
        failed_nodes=sorted(
            failed_node_counts.values(), key=lambda item: item.failures, reverse=True
        )[:20],
        llm_cost_estimate_usd=llm_cost_estimate_usd,
        llm_average_latency_ms=llm_average_latency_ms,
        conversation_quality=ConversationQualityMetric(
            total_conversations=total_conversations,
            recovered_conversations=recovered_conversations,
            avg_messages_per_conversation=avg_messages,
            fallback_rate=fallback_rate,
        ),
        timestamp=utc_now(),
    )


async def list_governance_audits(limit: int = 200) -> List[GovernanceAuditResponse]:
    audits = (
        await governance_audits_collection.find({})
        .sort("created_at", -1)
        .to_list(length=limit)
    )
    return [
        GovernanceAuditResponse.model_validate(serialize_document(item))
        for item in audits
    ]


async def receive_alert_webhook(payload: Dict[str, Any]) -> MessageResponse:
    await governance_audits_collection.insert_one(
        {
            "audit_id": f"gaudit_{ObjectId()}",
            "actor_user_id": None,
            "actor_email": "alertmanager",
            "action": "alert_webhook_received",
            "resource_type": "observability_alert",
            "resource_id": None,
            "details": payload,
            "created_at": utc_now(),
        }
    )
    return MessageResponse(message="Alert received")


async def check_mongodb_health() -> str:
    try:
        await client.admin.command("ping")
        return "connected"
    except Exception:
        return "disconnected"


async def check_redis_health() -> str:
    if not Config.REDIS_URL:
        return "memory_fallback"
    try:
        await _orchestration.startup()
        return "connected"
    except Exception:
        return "disconnected"


def get_runtime_status() -> Dict[str, Any]:
    return _runtime_registry.status()


def get_llm_status() -> str:
    return _llm_status
