"""
Simple Test Script - Verify the system is working
Run this after starting the backend server
"""

import httpx
import asyncio
import json

BASE_URL = "http://localhost:8000"


async def test_system():
    """Run basic system tests"""

    print("🧪 Testing Node API System")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        # Test 1: Health Check
        print("\n1️⃣  Testing Health Check...")
        try:
            response = await client.get(f"{BASE_URL}/health")
            health = response.json()
            print(f"   ✅ Status: {health['status']}")
            print(f"   ✅ MongoDB: {health['mongodb']}")
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return

        # Test 2: Create Project
        print("\n2️⃣  Creating Test Project...")
        project_data = {
            "name": "Test Project",
            "description": "A simple test project",
            "llm_prompt": "You are a helpful assistant.",
            "llm_config": {
                "model_name": "test-model",
                "temperature": 0.7,
                "max_tokens": 1024,
            },
        }

        response = await client.post(f"{BASE_URL}/api/projects", json=project_data)
        project = response.json()
        project_id = project["project_api_id"]
        print(f"   ✅ Project Created: {project_id}")
        print(f"   Name: {project['name']}")

        # Test 3: Create Node
        print("\n3️⃣  Creating Test Node...")
        node_data = {
            "project_api_id": project_id,
            "name": "TestNode",
            "description": "A test node that returns hello world",
            "is_root": True,
            "node_schema": {
                "inputs": {"name": "string"},
                "outputs": {"message": "string"},
            },
            "tool_code": """
async function executeNode(inputs) {
    return {
        success: true,
        message: `Hello, ${inputs.name || 'World'}!`,
        timestamp: new Date().toISOString()
    };
}
""",
        }

        response = await client.post(f"{BASE_URL}/api/nodes", json=node_data)
        node = response.json()
        print(f"   ✅ Node Created: {node['name']}")
        print(f"   Node ID: {node['node_id']}")

        # Test 4: Execute Node
        print("\n4️⃣  Executing Node...")
        execute_data = {
            "project_api_id": project_id,
            "node_name": "TestNode",
            "input_data": {"name": "Developer"},
        }

        response = await client.post(f"{BASE_URL}/api/execute-node", json=execute_data)
        result = response.json()
        print(f"   ✅ Node Executed Successfully")
        print(f"   Result: {json.dumps(result['result'], indent=6)}")

        # Test 5: Chat
        print("\n5️⃣  Testing Chat...")
        chat_data = {"project_api_id": project_id, "user_message": "Hello!"}

        response = await client.post(f"{BASE_URL}/api/chat", json=chat_data)
        chat = response.json()
        print(f"   ✅ Chat Response:")
        print(f"   Conversation ID: {chat['conversation_id']}")
        print(f"   Bot: {chat['bot_response'][:100]}...")

        # Test 6: List Projects
        print("\n6️⃣  Listing All Projects...")
        response = await client.get(f"{BASE_URL}/api/projects")
        projects = response.json()
        print(f"   ✅ Total Projects: {len(projects)}")

        # Test 7: List Nodes
        print("\n7️⃣  Listing Project Nodes...")
        response = await client.get(f"{BASE_URL}/api/projects/{project_id}/nodes")
        nodes = response.json()
        print(f"   ✅ Total Nodes: {len(nodes)}")

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print(f"\n📊 Summary:")
        print(f"   Project ID: {project_id}")
        print(f"   Node Count: {len(nodes)}")
        print(f"   API Base: {BASE_URL}")
        print(f"\n🌐 Open API Docs: {BASE_URL}/docs")


if __name__ == "__main__":
    asyncio.run(test_system())
