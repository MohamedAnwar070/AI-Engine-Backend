from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Generic, List, Literal, Optional, TypeVar, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


T = TypeVar("T")


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, protected_namespaces=()
    )


class LLMConfig(StrictModel):
    model_name: str = Field(
        default="gpt-4o-mini",
        min_length=1,
        validation_alias=AliasChoices("model_name", "llm_model_name"),
        serialization_alias="model_name",
    )
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    top_p: float = Field(default=0.9, ge=0, le=1)
    top_k: int = Field(default=40, ge=1)
    # Optional DB-driven LiteLLM binding. When set, chat for this project
    # uses that provider doc instead of the global default. When unset,
    # the global default llm_providers entry is used.
    provider_id: Optional[str] = Field(default=None, max_length=100)
    model_override: Optional[str] = Field(default=None, max_length=300)


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    llm_prompt: str = Field(min_length=1, max_length=50000)
    llm_config: LLMConfig = Field(default_factory=LLMConfig)
    template_id: Optional[str] = None
    template_variables: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "description", "llm_prompt")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned


class ProjectResponse(StrictModel):
    id: str = Field(alias="_id")
    project_api_id: str
    name: str
    description: str
    llm_prompt: str
    llm_config: LLMConfig
    created_at: datetime
    updated_at: datetime


class NodeSchema(StrictModel):
    inputs: Dict[str, str] = Field(default_factory=dict)
    outputs: Dict[str, str] = Field(default_factory=dict)


class HttpToolConfig(StrictModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "POST"
    url: str = Field(min_length=1, max_length=2000)
    headers: Dict[str, str] = Field(default_factory=dict)
    body_template: Optional[Any] = None


class NodeCreate(StrictModel):
    project_api_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_]+$")
    description: str = Field(min_length=1, max_length=5000)
    node_schema: NodeSchema = Field(
        validation_alias=AliasChoices("node_schema", "schema"),
        serialization_alias="node_schema",
    )
    runtime: Literal["javascript", "python", "http"] = "javascript"
    tool_code: str = Field(default="", max_length=100000)
    http_config: Optional[HttpToolConfig] = None
    is_root: bool = False
    dependencies: List[str] = Field(default_factory=list)

    @field_validator("project_api_id", "name", "description")
    @classmethod
    def strip_node_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned

    @field_validator("tool_code")
    @classmethod
    def strip_tool_code(cls, value: str) -> str:
        return (value or "").strip()

    @model_validator(mode="after")
    def validate_runtime_payload(self) -> "NodeCreate":
        if self.runtime == "http":
            if not self.http_config:
                raise ValueError("http_config is required when runtime is 'http'")
        elif not self.tool_code:
            raise ValueError("tool_code is required for javascript/python runtimes")
        return self


class NodeResponse(StrictModel):
    id: str = Field(alias="_id")
    node_id: str
    project_api_id: str
    name: str
    description: str
    node_schema: NodeSchema
    runtime: Literal["javascript", "python", "http"] = "javascript"
    tool_code: str = ""
    http_config: Optional[HttpToolConfig] = None
    is_root: bool
    dependencies: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ProjectBundleProject(StrictModel):
    """Portable project definition (no server ids required for import)."""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    llm_prompt: str = Field(min_length=1, max_length=50000)
    llm_config: LLMConfig = Field(default_factory=LLMConfig)


class ProjectBundleNode(StrictModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_]+$")
    description: str = Field(min_length=1, max_length=5000)
    node_schema: NodeSchema = Field(
        default_factory=NodeSchema,
        validation_alias=AliasChoices("node_schema", "schema"),
        serialization_alias="node_schema",
    )
    runtime: Literal["javascript", "python", "http"] = "javascript"
    tool_code: str = Field(default="", max_length=100000)
    http_config: Optional[HttpToolConfig] = None
    is_root: bool = False
    dependencies: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_runtime_payload(self) -> "ProjectBundleNode":
        if self.runtime == "http":
            if not self.http_config:
                raise ValueError("http_config is required when runtime is 'http'")
        elif not (self.tool_code or "").strip():
            raise ValueError("tool_code is required for javascript/python runtimes")
        return self


class ProjectBundle(StrictModel):
    """
    Canonical Perfect Engine package format (v2).

    Supports:
    - single project: { version, project, nodes }
    - multi project:  { version, projects: [{ project, nodes }, ...] }
    - legacy v1 export shapes used by the SPA
    """

    version: int = Field(default=2, ge=1)
    kind: Literal["perfect_engine_project", "perfect_engine_workspace"] = (
        "perfect_engine_project"
    )
    exported_at: Optional[datetime] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    project: Optional[ProjectBundleProject] = None
    nodes: List[ProjectBundleNode] = Field(default_factory=list)
    projects: List[Dict[str, Any]] = Field(default_factory=list)


class ProjectBundleImportResult(StrictModel):
    project_api_id: str
    name: str
    nodes_created: int
    nodes_skipped: int
    node_errors: List[str] = Field(default_factory=list)


class WorkspaceImportResult(StrictModel):
    projects_imported: int
    results: List[ProjectBundleImportResult] = Field(default_factory=list)


class ExecutionMetadata(StrictModel):
    execution_id: str
    status: Literal["success", "error", "timeout", "cached"]
    worker_id: Optional[str] = None
    cache_hit: bool = False
    attempts: int = Field(ge=1)
    duration_ms: float = Field(ge=0)
    timeout_ms: int = Field(ge=1)
    started_at: datetime
    completed_at: datetime
    cache_key: Optional[str] = None
    retry_reason: Optional[str] = None


class ExecuteNodeRequest(StrictModel):
    project_api_id: str = Field(min_length=1)
    node_name: str = Field(min_length=1)
    input_data: Dict[str, Any] = Field(default_factory=dict)
    tool_code: Optional[str] = None
    timeout_ms: Optional[int] = Field(default=None, ge=100, le=300000)
    cache_ttl_seconds: Optional[int] = Field(default=None, ge=0, le=86400)
    max_retries: Optional[int] = Field(default=None, ge=0, le=5)


class NodeExecutionResponse(StrictModel):
    node_id: str
    node_name: str
    result: Any
    executed_at: datetime
    metadata: ExecutionMetadata


class ExecutionHistoryResponse(StrictModel):
    id: str = Field(alias="_id")
    execution_id: str
    project_api_id: str
    node_id: str
    node_name: str
    source: Literal["api", "chat", "test"]
    conversation_id: Optional[str] = None
    test_run_id: Optional[str] = None
    input_data: Dict[str, Any] = Field(default_factory=dict)
    result: Any
    metadata: ExecutionMetadata
    replay_available: bool = True
    created_at: datetime


class NodeVersionResponse(StrictModel):
    id: str = Field(alias="_id")
    version_id: str
    node_id: str
    project_api_id: str
    version_number: int
    name: str
    description: str
    node_schema: NodeSchema
    tool_code: str
    is_root: bool
    dependencies: List[str] = Field(default_factory=list)
    created_at: datetime
    source: Literal["create", "update", "import", "manual"]


class ProjectTemplateResponse(StrictModel):
    id: Optional[str] = Field(
        default=None, alias="_id", validation_alias=AliasChoices("_id", "id")
    )
    template_id: str
    name: str
    category: str
    description: str
    llm_prompt: str
    suggested_nodes: List[Dict[str, Any]] = Field(default_factory=list)
    suggested_test_messages: List[str] = Field(default_factory=list)


class ProjectTemplateCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    llm_prompt: str = Field(min_length=1, max_length=50000)
    suggested_nodes: List[Dict[str, Any]] = Field(default_factory=list)
    suggested_test_messages: List[str] = Field(default_factory=list)


class ProjectTemplateUpdate(ProjectTemplateCreate):
    pass


class PromptPresetResponse(StrictModel):
    id: Optional[str] = Field(default=None, alias="_id")
    preset_id: str
    project_api_id: str
    name: str
    prompt: str
    test_messages: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    created_at: datetime


class PromptPresetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=50000)
    test_messages: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class GraphNodeResponse(StrictModel):
    node_id: str
    name: str
    is_root: bool
    dependencies: List[str] = Field(default_factory=list)
    execution_count: int = 0
    failure_count: int = 0


class GraphEdgeResponse(StrictModel):
    source: str
    target: str
    type: Literal["declared", "observed", "fallback"]
    weight: int = 1


class NodeGraphResponse(StrictModel):
    project_api_id: str
    nodes: List[GraphNodeResponse]
    edges: List[GraphEdgeResponse]


class SimulationIssue(StrictModel):
    level: Literal["info", "warning", "error"]
    code: str
    message: str
    node_id: Optional[str] = None


class SimulationResponse(StrictModel):
    project_api_id: str
    valid: bool
    root_node_id: Optional[str] = None
    total_nodes: int
    issues: List[SimulationIssue] = Field(default_factory=list)
    suggested_entrypoints: List[str] = Field(default_factory=list)


class ToolAuditResponse(StrictModel):
    id: str = Field(alias="_id")
    audit_id: str
    project_api_id: str
    conversation_id: Optional[str] = None
    user_message: str
    llm_action: str
    node_name: Optional[str] = None
    confidence_score: float
    fallback_used: bool = False
    recovery_used: bool = False
    reason: Optional[str] = None
    created_at: datetime


class ConversationRecoveryResponse(StrictModel):
    project_api_id: str
    session_id: Optional[str] = None
    recovered: bool
    conversation_id: Optional[str] = None
    latest_user_message: Optional[str] = None
    latest_node: Optional[str] = None
    recommended_prompt: Optional[str] = None
    preserved_context: Optional[Dict[str, Any]] = None


class ReplayExecutionRequest(StrictModel):
    timeout_ms: Optional[int] = Field(default=None, ge=100, le=300000)
    cache_ttl_seconds: Optional[int] = Field(default=0, ge=0, le=86400)
    max_retries: Optional[int] = Field(default=None, ge=0, le=5)


class FailedNodeMetric(StrictModel):
    node_name: str
    failures: int
    latest_failure_at: Optional[datetime] = None


class ProjectHealthMetric(StrictModel):
    project_api_id: str
    project_name: str
    health_score: float
    success_rate: float
    avg_latency_ms: float
    failed_runs: int
    last_activity_at: Optional[datetime] = None


class ConversationQualityMetric(StrictModel):
    total_conversations: int
    recovered_conversations: int
    avg_messages_per_conversation: float
    fallback_rate: float


class ControlPlaneOverviewResponse(StrictModel):
    project_health: List[ProjectHealthMetric]
    failed_nodes: List[FailedNodeMetric]
    llm_cost_estimate_usd: float
    llm_average_latency_ms: float
    conversation_quality: ConversationQualityMetric
    timestamp: datetime


class ChatRequest(StrictModel):
    project_api_id: str = Field(min_length=1)
    user_message: str = Field(min_length=1, max_length=10000)
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    force_new: bool = False

    @field_validator("project_api_id", "user_message")
    @classmethod
    def strip_chat_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned


class ConversationMessage(StrictModel):
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime
    node_executed: Optional[str] = None
    node_result: Optional[Any] = None
    execution_metadata: Optional[ExecutionMetadata] = None


class ConversationResponse(StrictModel):
    id: str = Field(alias="_id")
    conversation_id: str
    project_api_id: str
    session_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    is_test: bool = False
    reset_from: Optional[str] = None
    preserved_context: Optional[Dict[str, Any]] = None
    messages: List[ConversationMessage] = Field(default_factory=list)


class ChatResponse(StrictModel):
    conversation_id: str
    bot_response: str
    node_executed: Optional[str] = None
    node_result: Optional[Any] = None
    execution_metadata: Optional[ExecutionMetadata] = None
    debug_info: Optional[str] = None
    conversation_reset: bool = False
    preserved_context: Optional[Dict[str, Any]] = None


class ProjectTestRequest(StrictModel):
    project_api_id: Optional[str] = None
    test_messages: List[str] = Field(min_length=1)
    show_node_execution: bool = True
    execution_mode: Literal["sync", "async", "auto"] = "auto"

    @field_validator("test_messages")
    @classmethod
    def validate_test_messages(cls, values: List[str]) -> List[str]:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if not cleaned:
            raise ValueError("At least one non-empty test message is required")
        return cleaned


class ProjectTestResult(StrictModel):
    test_number: int
    user_message: str
    bot_response: Optional[str] = None
    action: Optional[Literal["chat", "call_node"]] = None
    node_executed: Optional[str] = None
    node_result: Optional[Any] = None
    execution_metadata: Optional[ExecutionMetadata] = None
    success: bool
    error: Optional[str] = None


class ProjectTestResponse(StrictModel):
    status: Literal["completed"] = "completed"
    execution_mode: Literal["sync"] = "sync"
    project_api_id: str
    project_name: str
    total_tests: int
    successful_tests: int
    failed_tests: int
    test_results: List[ProjectTestResult]
    conversation_id: str
    started_at: datetime
    completed_at: datetime
    duration_ms: float = Field(ge=0)


class ProjectTestQueuedResponse(StrictModel):
    status: Literal["queued", "running"]
    execution_mode: Literal["async"] = "async"
    test_run_id: str
    project_api_id: str
    project_name: str
    total_tests: int
    queued_at: datetime
    poll_url: str


class ProjectTestRunResponse(StrictModel):
    id: str = Field(alias="_id")
    test_run_id: str
    status: Literal["queued", "running", "completed", "failed"]
    project_api_id: str
    project_name: str
    total_tests: int
    successful_tests: int = 0
    failed_tests: int = 0
    queued_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    conversation_id: Optional[str] = None
    result: Optional[ProjectTestResponse] = None
    error: Optional[str] = None


ProjectTestRouteResponse = Union[ProjectTestResponse, ProjectTestQueuedResponse]


class StatsResponse(StrictModel):
    total_projects: int
    active_nodes: int
    messages_processed: int
    total_executions: int
    successful_executions: int
    failed_executions: int
    cached_executions: int
    average_latency_ms: float
    success_rate: float
    queued_test_runs: int
    running_test_runs: int
    timestamp: datetime


class RuntimeStatusResponse(StrictModel):
    javascript: Dict[str, Any] = Field(default_factory=dict)
    python: Dict[str, Any] = Field(default_factory=dict)
    http: Dict[str, Any] = Field(default_factory=dict)
    supported: List[str] = Field(default_factory=list)


class HealthResponse(StrictModel):
    status: str
    mongodb: str
    redis: str
    llm_provider: str
    llm_status: str = "unknown"
    environment: str
    release_version: str
    auth_enabled: bool
    runtimes: Optional[Dict[str, Any]] = None
    timestamp: datetime


class RootResponse(StrictModel):
    message: str
    version: str
    docs: str
    health: str


class MessageResponse(StrictModel):
    message: str


class AuthLoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)


class UserCreate(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "editor", "viewer"] = "viewer"


class UserResponse(StrictModel):
    id: str = Field(alias="_id")
    user_id: str
    email: str
    full_name: str
    role: Literal["admin", "editor", "viewer"]
    is_active: bool
    created_at: datetime


class TokenResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse


class GovernanceAuditResponse(StrictModel):
    id: str = Field(alias="_id")
    audit_id: str
    actor_user_id: Optional[str] = None
    actor_email: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ErrorDetail(StrictModel):
    code: str
    message: str
    details: Optional[Any] = None
    request_id: Optional[str] = None
    timestamp: datetime


class ErrorEnvelope(StrictModel):
    success: bool = False
    error: ErrorDetail


class ApiEnvelope(StrictModel, Generic[T]):
    success: bool = True
    data: T


# ================================
# DB-driven LiteLLM providers
# ================================

LLM_PROVIDER_KEYS = Literal[
    "gemini", "groq", "openrouter", "github", "openai", "anthropic",
    "deepseek", "mistral", "custom",
]


class LLMProviderLimits(StrictModel):
    rpm: Optional[int] = Field(default=None, ge=1)
    rpd: Optional[int] = Field(default=None, ge=1)
    tpm: Optional[int] = Field(default=None, ge=1)
    context_window: Optional[int] = Field(default=None, ge=1)


class LLMProviderBase(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    provider: LLM_PROVIDER_KEYS = "gemini"
    model: str = Field(min_length=1, max_length=300)
    base_url: str = Field(default="", max_length=2000)
    api_key_env: str = Field(default="", max_length=100)
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    # Custom gateways don't all speak Bearer auth:
    #   bearer    -> Authorization: Bearer <key> (OpenAI-compatible default)
    #   x-api-key -> <auth_header or x-api-key>: <key>
    #   query     -> ?<auth_query_param or key>=<key>
    #   none      -> no key sent (local / unauthenticated endpoints)
    auth_type: Literal["bearer", "x-api-key", "query", "none"] = "bearer"
    auth_header: str = Field(default="", max_length=100)
    auth_query_param: str = Field(default="", max_length=100)
    # Wire format spoken at base_url (see screenshot-style selector):
    #   chat-completions   -> POST {base}/chat/completions (OpenAI-compatible)
    #   anthropic-messages -> POST {base}/v1/messages (Anthropic Messages API)
    #   responses          -> POST {base}/responses (OpenAI Responses API)
    api_format: Literal["chat-completions", "anthropic-messages", "responses"] = (
        "chat-completions"
    )
    default_temperature: float = Field(default=0.4, ge=0, le=2)
    default_max_tokens: int = Field(default=1024, ge=1, le=128000)
    default_top_p: float = Field(default=0.9, ge=0, le=1)
    is_active: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    tier: Literal["free", "paid", "trial"] = "free"
    limits: LLMProviderLimits = Field(default_factory=LLMProviderLimits)
    limits_note: str = Field(default="", max_length=2000)
    docs_url: str = Field(default="", max_length=2000)
    cost_per_1k_input_usd: Optional[float] = Field(default=None, ge=0)
    cost_per_1k_output_usd: Optional[float] = Field(default=None, ge=0)

    @field_validator("name", "model")
    @classmethod
    def strip_llm_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned

    @field_validator("base_url", "api_key_env", "docs_url", "limits_note")
    @classmethod
    def strip_optional_llm_text(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("auth_header", "auth_query_param")
    @classmethod
    def strip_auth_text(cls, value: str) -> str:
        return (value or "").strip()

    @model_validator(mode="after")
    def validate_custom_endpoint(self) -> "LLMProviderBase":
        if self.provider == "custom" and not (self.base_url or "").strip():
            raise ValueError("base_url is required when provider is 'custom'")
        return self


class LLMProviderCreate(LLMProviderBase):
    api_key: str = Field(default="", max_length=5000)

    @field_validator("api_key")
    @classmethod
    def strip_api_key(cls, value: str) -> str:
        return (value or "").strip()


class LLMProviderUpdate(StrictModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    provider: Optional[LLM_PROVIDER_KEYS] = None
    model: Optional[str] = Field(default=None, min_length=1, max_length=300)
    base_url: Optional[str] = Field(default=None, max_length=2000)
    api_key_env: Optional[str] = Field(default=None, max_length=100)
    api_key: Optional[str] = Field(default=None, max_length=5000)
    extra_params: Optional[Dict[str, Any]] = None
    auth_type: Optional[Literal["bearer", "x-api-key", "query", "none"]] = None
    auth_header: Optional[str] = Field(default=None, max_length=100)
    auth_query_param: Optional[str] = Field(default=None, max_length=100)
    api_format: Optional[Literal["chat-completions", "anthropic-messages", "responses"]] = None
    default_temperature: Optional[float] = Field(default=None, ge=0, le=2)
    default_max_tokens: Optional[int] = Field(default=None, ge=1, le=128000)
    default_top_p: Optional[float] = Field(default=None, ge=0, le=1)
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10000)
    tier: Optional[Literal["free", "paid", "trial"]] = None
    limits: Optional[LLMProviderLimits] = None
    limits_note: Optional[str] = Field(default=None, max_length=2000)
    docs_url: Optional[str] = Field(default=None, max_length=2000)
    cost_per_1k_input_usd: Optional[float] = Field(default=None, ge=0)
    cost_per_1k_output_usd: Optional[float] = Field(default=None, ge=0)


class LLMProviderResponse(StrictModel):
    id: str = Field(alias="_id")
    provider_id: str
    name: str
    provider: str
    model: str
    base_url: str = ""
    api_key_env: str = ""
    has_api_key: bool = False
    api_key_hint: str = ""
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    auth_type: str = "bearer"
    auth_header: str = ""
    auth_query_param: str = ""
    api_format: str = "chat-completions"
    default_temperature: float = 0.4
    default_max_tokens: int = 1024
    default_top_p: float = 0.9
    is_active: bool = True
    is_default: bool = False
    priority: int = 100
    tier: str = "free"
    limits: LLMProviderLimits = Field(default_factory=LLMProviderLimits)
    limits_note: str = ""
    docs_url: str = ""
    cost_per_1k_input_usd: Optional[float] = None
    cost_per_1k_output_usd: Optional[float] = None
    total_requests: int = 0
    total_errors: int = 0
    last_used_at: Optional[datetime] = None
    last_tested_at: Optional[datetime] = None
    last_test_status: Optional[str] = None
    last_test_error: Optional[str] = None
    last_latency_ms: Optional[float] = None
    created_at: datetime
    updated_at: datetime


class LLMProviderTestResponse(StrictModel):
    provider_id: str
    ok: bool
    latency_ms: float
    model: str
    sample_text: str = ""
    error: Optional[str] = None
    tested_at: datetime


class LLMProviderProjectBinding(StrictModel):
    project_api_id: str = Field(min_length=1, max_length=100)

    @field_validator("project_api_id")
    @classmethod
    def strip_binding_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned
