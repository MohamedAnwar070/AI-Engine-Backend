"""Generic tool router — no product-specific intents."""

from app.core.tool_router import route_message, score_node


def _nodes_sample():
    return [
        {
            "name": "inventory_lookup",
            "description": "Find warehouse SKU inventory levels by product code",
            "node_schema": {"inputs": {"product_code": "string"}, "outputs": {}},
            "is_root": False,
        },
        {
            "name": "weather_forecast",
            "description": "Get weather forecast for a city",
            "node_schema": {"inputs": {"city": "string"}, "outputs": {}},
            "is_root": True,
        },
        {
            "name": "payment_refund",
            "description": "Issue a refund for an order id",
            "node_schema": {"inputs": {"order_id": "string"}, "outputs": {}},
            "is_root": False,
        },
    ]


def test_routes_by_node_metadata_not_hardcoded_intents():
    nodes = _nodes_sample()
    decision = route_message("check inventory for product SKU", nodes)
    assert decision.action == "call_node"
    assert decision.node_name == "inventory_lookup"
    assert decision.strategy in {"lexical", "root_fallback"}


def test_routes_weather_from_description_words():
    nodes = _nodes_sample()
    decision = route_message("what is the weather forecast in Paris", nodes)
    assert decision.action == "call_node"
    assert decision.node_name == "weather_forecast"


def test_no_aidoc_hardcoding_required_for_categories_style_node():
    nodes = [
        {
            "name": "aidoc_categories",
            "description": "List all active document categories folders available for browsing",
            "node_schema": {"inputs": {}, "outputs": {}},
            "is_root": False,
        },
        {
            "name": "aidoc_semantic_search",
            "description": "Semantic RAG search over indexed documents",
            "node_schema": {"inputs": {"query": "string"}, "outputs": {}},
            "is_root": False,
        },
    ]
    # Words come from the node description/name — not INTENT_HINTS
    decision = route_message("list document categories folders", nodes)
    assert decision.action == "call_node"
    assert decision.node_name == "aidoc_categories"


def test_clarify_lists_available_tools_dynamically():
    nodes = _nodes_sample()
    decision = route_message("zzzz unrelated quantum xyzzy", nodes)
    # root fallback may fire because weather is root — either root or clarify is ok
    assert decision.action in {"call_node", "chat"}
    if decision.action == "chat":
        assert "inventory_lookup" in decision.text_response
        assert "weather_forecast" in decision.text_response


def test_score_node_is_relative():
    nodes = _nodes_sample()
    inv = score_node("inventory warehouse SKU", nodes[0])
    pay = score_node("inventory warehouse SKU", nodes[2])
    assert inv > pay
