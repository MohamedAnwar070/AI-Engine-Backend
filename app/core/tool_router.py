"""
Generic tool router (domain-agnostic).

Architecture principle
----------------------
Tools are defined by the project (name, description, node_schema.inputs).
We never hardcode product intents like "categories" or "login".

Routing layers (highest → lowest authority):
1. Real LLM with tool catalog (OpenAI / local Transformers / GGUF)
2. This lexical ranker over *whatever nodes the project exposes*
3. Root-node fallback (is_root=true) when score is weak but an entrypoint exists
4. Clarifying chat listing available tools (generated from nodes)

Humans can ask anything; we only score against registered tool metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Generic English glue — not product vocabulary
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "am",
        "i",
        "me",
        "my",
        "we",
        "you",
        "your",
        "it",
        "this",
        "that",
        "these",
        "those",
        "do",
        "does",
        "did",
        "please",
        "can",
        "could",
        "would",
        "should",
        "will",
        "just",
        "about",
        "from",
        "into",
        "any",
        "all",
        "some",
        "more",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "why",
        "there",
        "here",
        "also",
        "than",
        "then",
        "them",
        "they",
        "their",
        "our",
        "us",
        "at",
        "as",
        "by",
        "if",
        "so",
        "not",
        "no",
        "yes",
        "ok",
        "okay",
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank",
        "need",
        "want",
        "get",
        "give",
        "make",
        "help",
        "tell",
        "show",
        "list",
        "find",
        "see",
        "look",
        "use",
        "using",
        "call",
        "run",
        "try",
    }
)

# Input field names that typically accept the full free-text utterance
_OPEN_TEXT_INPUTS = frozenset(
    {
        "query",
        "q",
        "message",
        "text",
        "prompt",
        "input",
        "user_message",
        "search",
        "question",
        "content",
        "term",
        "keywords",
    }
)


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", (text or "").lower())


def content_tokens(text: str) -> set[str]:
    return {t for t in tokenize(text) if len(t) > 2 and t not in _STOPWORDS}


def node_vocab(node: Dict[str, Any]) -> Dict[str, float]:
    """
    Weighted vocabulary derived only from the node definition.
    Weights: name > input keys > description tokens.
    """
    weights: Dict[str, float] = {}

    def add(token: str, weight: float) -> None:
        if len(token) <= 2 or token in _STOPWORDS:
            return
        weights[token] = max(weights.get(token, 0.0), weight)

    name = str(node.get("name") or "")
    # Split identifiers: aidoc_semantic_search → aidoc, semantic, search
    for part in re.split(r"[_\-\s]+", name.lower()):
        add(part, 4.0)
        # light stem-ish: categories/category
        if part.endswith("ies") and len(part) > 4:
            add(part[:-3] + "y", 3.5)
        if part.endswith("s") and len(part) > 3:
            add(part[:-1], 3.0)

    for key in (node.get("node_schema") or {}).get("inputs", {}) or {}:
        for part in re.split(r"[_\-\s]+", str(key).lower()):
            add(part, 2.5)

    for token in content_tokens(str(node.get("description") or "")):
        add(token, 1.2)

    return weights


def score_node(user_message: str, node: Dict[str, Any]) -> float:
    msg_tokens = content_tokens(user_message)
    if not msg_tokens:
        return 0.0

    vocab = node_vocab(node)
    if not vocab:
        return 0.0

    score = 0.0
    for token, weight in vocab.items():
        if token in msg_tokens:
            score += weight
        else:
            # substring match for longer tokens only (avoid noisy short hits)
            if len(token) >= 5 and any(token in m or m in token for m in msg_tokens if len(m) >= 4):
                score += weight * 0.45

    # Phrase boost: multi-token node name fully present as words in message
    name_parts = [
        p
        for p in re.split(r"[_\-\s]+", str(node.get("name") or "").lower())
        if len(p) > 2 and p not in _STOPWORDS
    ]
    if len(name_parts) >= 2:
        msg_lower = user_message.lower()
        if all(p in msg_lower for p in name_parts):
            score += 3.0

    # Soft boost for entrypoints when user is exploratory
    if node.get("is_root"):
        exploratory = {"list", "show", "start", "begin", "help", "what", "options"}
        if any(w in user_message.lower().split() for w in exploratory):
            score += 0.8

    return round(score, 3)


def extract_inputs(user_message: str, node: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort input fill from free text — schema-driven, not domain-driven."""
    schema_inputs = list((node.get("node_schema") or {}).get("inputs", {}) or {})
    if not schema_inputs:
        return {}

    inputs: Dict[str, Any] = {}
    message = user_message or ""
    lower = message.lower()

    # key: value / key=value patterns
    for key in schema_inputs:
        pattern = re.compile(
            rf"\b{re.escape(key)}\b\s*[:=]\s*([^\n,;]+)",
            re.IGNORECASE,
        )
        match = pattern.search(message)
        if match:
            inputs[key] = match.group(1).strip().strip("\"'")

    # email heuristic if field looks like email
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", message)
    if email_match:
        for key in schema_inputs:
            if "email" in key.lower() and key not in inputs:
                inputs[key] = email_match.group(0)

    # Open-text fields get the full utterance if still empty
    for key in schema_inputs:
        if key in inputs:
            continue
        key_l = key.lower()
        if key_l in _OPEN_TEXT_INPUTS or any(
            k in key_l for k in ("query", "message", "text", "prompt", "search")
        ):
            inputs[key] = message
        else:
            inputs[key] = ""

    # If exactly one required-looking field left empty and no open-text, put message there
    empty = [k for k, v in inputs.items() if v == ""]
    if len(empty) == 1 and not any(
        k.lower() in _OPEN_TEXT_INPUTS for k in schema_inputs
    ):
        inputs[empty[0]] = message

    return inputs


@dataclass
class ToolRouteDecision:
    action: str  # call_node | chat
    node_name: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    text_response: str = ""
    confidence: float = 0.0
    ranked: List[Tuple[float, str]] = field(default_factory=list)
    strategy: str = "none"  # lexical | root_fallback | clarify | greeting | summarize


def _tool_catalog_line(nodes: Sequence[Dict[str, Any]], *, limit: int = 12) -> str:
    names = [str(n.get("name")) for n in nodes if n.get("name")]
    if not names:
        return "no tools registered"
    shown = names[:limit]
    extra = len(names) - len(shown)
    text = ", ".join(shown)
    if extra > 0:
        text += f" (+{extra} more)"
    return text


def route_message(
    user_message: str,
    nodes: Sequence[Dict[str, Any]],
    *,
    project_name: str = "assistant",
    project_description: str = "",
    min_score: float = 2.0,
    margin: float = 0.75,
) -> ToolRouteDecision:
    """
    Domain-agnostic tool selection from free text + node metadata only.
    """
    message = (user_message or "").strip()
    lower = message.lower()
    node_list = [n for n in nodes if isinstance(n, dict) and n.get("name")]

    # Pure greetings
    greetings = {"hi", "hello", "hey", "greetings", "good morning", "good evening", "good afternoon"}
    if lower in greetings or (len(lower.split()) <= 2 and any(lower.startswith(g) for g in greetings)):
        desc = f" {project_description}" if project_description else ""
        return ToolRouteDecision(
            action="chat",
            text_response=(
                f"Hello! I'm your {project_name} assistant.{desc} "
                f"Available tools: {_tool_catalog_line(node_list)}."
            ),
            strategy="greeting",
        )

    if not node_list:
        return ToolRouteDecision(
            action="chat",
            text_response="This project has no tools registered yet. Add nodes to enable actions.",
            strategy="clarify",
        )

    ranked_nodes = sorted(
        ((score_node(message, node), node) for node in node_list),
        key=lambda item: item[0],
        reverse=True,
    )
    ranked = [(s, str(n.get("name"))) for s, n in ranked_nodes if s > 0]

    best_score, best_node = ranked_nodes[0]
    second_score = ranked_nodes[1][0] if len(ranked_nodes) > 1 else 0.0

    # Clear winner
    if best_score >= min_score and (best_score - second_score) >= margin:
        return ToolRouteDecision(
            action="call_node",
            node_name=str(best_node.get("name")),
            inputs=extract_inputs(message, best_node),
            text_response=f"Using {best_node.get('name')}…",
            confidence=min(1.0, best_score / 10.0),
            ranked=ranked[:5],
            strategy="lexical",
        )

    # Close race but best is usable
    if best_score >= min_score * 1.25:
        return ToolRouteDecision(
            action="call_node",
            node_name=str(best_node.get("name")),
            inputs=extract_inputs(message, best_node),
            text_response=f"Using {best_node.get('name')}…",
            confidence=min(1.0, best_score / 12.0),
            ranked=ranked[:5],
            strategy="lexical",
        )

    # Weak match → root entrypoint if any
    root = next((n for n in node_list if n.get("is_root")), None)
    if root is not None:
        return ToolRouteDecision(
            action="call_node",
            node_name=str(root.get("name")),
            inputs=extract_inputs(message, root),
            text_response=f"Routing via entrypoint {root.get('name')}…",
            confidence=0.35,
            ranked=ranked[:5],
            strategy="root_fallback",
        )

    # Clarify with live catalog (never invent domain options)
    top = ", ".join(f"{name} ({score:.1f})" for score, name in ranked[:5]) or "none matched well"
    return ToolRouteDecision(
        action="chat",
        text_response=(
            f"I'm not sure which tool fits that request. "
            f"Available tools: {_tool_catalog_line(node_list)}. "
            f"Closest matches: {top}. "
            f"Try naming the tool or describing the action using words from a tool description."
        ),
        confidence=0.0,
        ranked=ranked[:5],
        strategy="clarify",
    )
