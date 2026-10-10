import asyncio

from app.runtime.registry import build_default_runtime_registry


async def main() -> None:
    registry = build_default_runtime_registry()
    await registry.start()
    js = await registry.execute(
        "javascript",
        code="async function executeNode(i){ return { success: true, sum: i.a + i.b }; }",
        input_data={"a": 2, "b": 3},
        timeout_ms=10000,
    )
    py = await registry.execute(
        "python",
        code='def execute_node(i):\n    return {"success": True, "sum": i["a"] + i["b"]}\n',
        input_data={"a": 2, "b": 3},
        timeout_ms=10000,
    )
    print("js", js.get("ok"), js.get("result"))
    print("py", py.get("ok"), py.get("result"))
    print("status", registry.status())
    await registry.stop()
    print("python_workers_after_stop", registry.status()["python"]["active_workers"])


if __name__ == "__main__":
    asyncio.run(main())
