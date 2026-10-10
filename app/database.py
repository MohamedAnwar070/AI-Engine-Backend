from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING

from app.config import Config


client = AsyncIOMotorClient(Config.MONGODB_URL)
db = client[Config.DATABASE_NAME]

projects_collection = db["projects"]
nodes_collection = db["nodes"]
conversations_collection = db["conversations"]
node_executions_collection = db["node_executions"]
test_runs_collection = db["test_runs"]
node_versions_collection = db["node_versions"]
tool_audits_collection = db["tool_audits"]
prompt_presets_collection = db["prompt_presets"]
project_templates_collection = db["project_templates"]
users_collection = db["users"]
governance_audits_collection = db["governance_audits"]
llm_providers_collection = db["llm_providers"]


async def ensure_indexes() -> None:
    await node_executions_collection.create_index(
        [("execution_id", ASCENDING)],
        unique=True,
        name="node_executions_execution_id_idx",
    )
    await node_executions_collection.create_index(
        [("project_api_id", ASCENDING), ("created_at", ASCENDING)],
        name="node_executions_project_api_id_idx",
    )
    await test_runs_collection.create_index(
        [("test_run_id", ASCENDING)],
        unique=True,
        name="test_runs_test_run_id_idx",
    )
    await node_versions_collection.create_index(
        [("node_id", ASCENDING), ("version_number", ASCENDING)],
        unique=True,
        name="node_versions_node_version_idx",
    )
    await tool_audits_collection.create_index(
        [("project_api_id", ASCENDING), ("created_at", ASCENDING)],
        name="tool_audits_project_created_idx",
    )
    await prompt_presets_collection.create_index(
        [("project_api_id", ASCENDING), ("created_at", ASCENDING)],
        name="prompt_presets_project_created_idx",
    )
    await project_templates_collection.create_index(
        [("template_id", ASCENDING)],
        unique=True,
        name="project_templates_template_id_idx",
    )
    await users_collection.create_index(
        [("email", ASCENDING)],
        unique=True,
        name="users_email_idx",
    )
    await governance_audits_collection.create_index(
        [("created_at", ASCENDING)],
        name="governance_audits_created_idx",
    )
    await llm_providers_collection.create_index(
        [("provider_id", ASCENDING)],
        unique=True,
        name="llm_providers_provider_id_idx",
    )
    await llm_providers_collection.create_index(
        [("is_active", ASCENDING), ("priority", ASCENDING)],
        name="llm_providers_active_priority_idx",
    )
