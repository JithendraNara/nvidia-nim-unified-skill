#!/usr/bin/env python3
"""Unified planner and request builder for NVIDIA NIM OCR/layout/rerank tasks."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "references" / "nim-capabilities.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        return json.load(handle)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def keyword_score(query: str, keywords: list[str]) -> int:
    normalized = normalize_text(query)
    score = 0
    for keyword in keywords:
        token = normalize_text(keyword)
        if token and token in normalized:
            score += max(1, len(token.split()))
    return score


def detect_flags(query: str) -> dict[str, bool]:
    normalized = normalize_text(query)
    phrase_sets = {
        "wants_text": [
            "ocr",
            "extract text",
            "read text",
            "transcribe image",
            "document text",
            "invoice text",
            "receipt text"
        ],
        "wants_table": [
            "table",
            "cell",
            "row",
            "column",
            "grid",
            "table structure"
        ],
        "wants_chart": [
            "chart",
            "graph",
            "legend",
            "axis",
            "xlabel",
            "ylabel",
            "value label"
        ],
        "wants_layout": [
            "layout",
            "page elements",
            "paragraph",
            "header",
            "footer",
            "title block",
            "segment page"
        ],
        "wants_rank": [
            "rerank",
            "rank passages",
            "relevance",
            "most relevant",
            "best chunk",
            "search results"
        ]
    }
    return {
        flag: any(phrase in normalized for phrase in phrases)
        for flag, phrases in phrase_sets.items()
    }


def select_workflow(query: str, flags: dict[str, bool], inputs: dict[str, Any]) -> tuple[str | None, list[str], list[str]]:
    reasons: list[str] = []
    if flags["wants_rank"] and (flags["wants_text"] or bool(inputs.get("image_urls"))):
        reasons.append("Task mixes text extraction and relevance ranking.")
        return "ocr_then_rerank", ["ocr", "rerank"], reasons
    if flags["wants_text"] and flags["wants_table"]:
        reasons.append("Task asks for text extraction with table preservation.")
        return "layout_aware_table_extraction", ["page_elements", "table_structure", "ocr"], reasons
    if flags["wants_text"] and flags["wants_chart"]:
        reasons.append("Task asks for text extraction with chart-aware understanding.")
        return "chart_aware_extraction", ["page_elements", "graphic_elements", "ocr"], reasons
    if flags["wants_layout"] and flags["wants_table"]:
        reasons.append("Task asks for layout and table structure together.")
        return None, ["page_elements", "table_structure"], reasons
    if flags["wants_layout"] and flags["wants_chart"]:
        reasons.append("Task asks for layout and chart annotations together.")
        return None, ["page_elements", "graphic_elements"], reasons
    return None, [], reasons


def select_capability(query: str, catalog: dict[str, Any]) -> tuple[str, dict[str, int]]:
    scores: dict[str, int] = {}
    for name, capability in catalog["capabilities"].items():
        scores[name] = keyword_score(query, capability.get("keywords", []))
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        if "http" in query.lower() or "image" in query.lower() or "document" in query.lower():
            return "ocr", scores
        return "rerank", scores
    return best, scores


def plan_task(task_query: str, catalog: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    flags = detect_flags(task_query)
    workflow_id, workflow, reasons = select_workflow(task_query, flags, inputs)
    if workflow:
        primary = workflow[-1] if workflow[-1] == "rerank" else workflow[0]
    else:
        primary, scores = select_capability(task_query, catalog)
        workflow = [primary]
        scores = scores
    if not workflow_id:
        primary, scores = select_capability(task_query, catalog)
        if workflow == [primary]:
            reasons.append(f"Highest keyword score matched `{primary}`.")
    else:
        _, scores = select_capability(task_query, catalog)
    required_inputs = catalog["capabilities"][workflow[0]]["request"]["required_inputs"]
    missing_inputs = []
    if "image_urls" in required_inputs and not inputs.get("image_urls"):
        missing_inputs.append("image_url")
    if "query_text" in required_inputs and not inputs.get("query_text"):
        missing_inputs.append("query_text")
    if "passages" in required_inputs and not inputs.get("passages"):
        missing_inputs.append("passage")
    return {
        "task_query": task_query,
        "workflow_id": workflow_id,
        "workflow": workflow,
        "primary_capability": primary,
        "scores": scores,
        "flags": flags,
        "reasoning": reasons,
        "ready_to_invoke_primary": not missing_inputs,
        "missing_primary_inputs": missing_inputs
    }


def load_runtime_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return load_json(Path(path))


def join_url(base_or_url: str, path: str) -> str:
    if base_or_url.endswith(path):
        return base_or_url
    if base_or_url.endswith("/"):
        base_or_url = base_or_url[:-1]
    return f"{base_or_url}{path}"


def resolve_capability_config(capability_name: str, capability: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    override = runtime.get(capability_name, {})
    url = override.get("url")
    if not url:
        fixed = capability.get("fixed_url")
        if fixed:
          url = fixed
        else:
            env_name = capability.get("endpoint_env")
            if env_name:
                env_value = os.getenv(env_name)
                if env_value:
                    url = join_url(env_value, capability["path"])
    if not url:
        raise SystemExit(f"Missing endpoint URL for `{capability_name}`.")

    auth_env = override.get("bearer_env") or capability.get("auth_env")
    token = override.get("bearer_token")
    if not token and auth_env:
        token = os.getenv(auth_env)

    return {
        "url": url,
        "token": token,
        "auth_env": auth_env
    }


def build_body(capability_name: str, capability: dict[str, Any], args: argparse.Namespace, runtime: dict[str, Any]) -> dict[str, Any]:
    defaults = capability["request"].get("defaults", {})
    if capability_name == "rerank":
        passages = [{"text": passage} for passage in (args.passage or [])]
        if not args.query_text or not passages:
            raise SystemExit("`rerank` requires --query-text and at least one --passage.")
        model = args.model or runtime.get("rerank", {}).get("model") or defaults["model"]
        return {
            "model": model,
            "query": {"text": args.query_text},
            "passages": passages,
            "truncate": args.truncate or defaults["truncate"]
        }

    image_urls = args.image_url or []
    if not image_urls:
        raise SystemExit(f"`{capability_name}` requires at least one --image-url.")
    body: dict[str, Any] = {
        "input": [{"type": "image_url", "url": url} for url in image_urls]
    }
    if capability_name == "ocr":
        merge_levels = args.merge_level or defaults.get("merge_levels")
        if merge_levels:
            body["merge_levels"] = merge_levels
        return body

    body["confidence_threshold"] = (
        args.confidence_threshold
        if args.confidence_threshold is not None
        else defaults.get("confidence_threshold")
    )
    body["nms_threshold"] = (
        args.nms_threshold
        if args.nms_threshold is not None
        else defaults.get("nms_threshold")
    )
    return body


def build_request(capability_name: str, args: argparse.Namespace, catalog: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    capability = catalog["capabilities"][capability_name]
    resolved = resolve_capability_config(capability_name, capability, runtime)
    body = build_body(capability_name, capability, args, runtime)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    if resolved["token"]:
        headers["Authorization"] = f"Bearer {resolved['token']}"
    return {
        "capability": capability_name,
        "method": capability["method"],
        "url": resolved["url"],
        "headers": headers,
        "body": body,
        "auth_env": resolved["auth_env"]
    }


def invoke_request(request_plan: dict[str, Any]) -> dict[str, Any]:
    body_bytes = json.dumps(request_plan["body"]).encode("utf-8")
    request = urllib.request.Request(
        request_plan["url"],
        data=body_bytes,
        method=request_plan["method"],
        headers=request_plan["headers"]
    )
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read().decode("utf-8")
            try:
                parsed: Any = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
            return {
                "status": response.status,
                "response": parsed
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return {
            "status": exc.code,
            "response": parsed
        }


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified planner and request builder for NVIDIA NIM tasks.")
    parser.add_argument("--catalog", default=str(CATALOG_PATH), help="Path to nim-capabilities.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Plan a capability or workflow from a free-form task.")
    plan_parser.add_argument("--task-query", required=True)
    plan_parser.add_argument("--image-url", action="append", default=[])
    plan_parser.add_argument("--query-text")
    plan_parser.add_argument("--passage", action="append", default=[])

    for name in ("build-request", "invoke"):
        command_parser = subparsers.add_parser(name, help=f"{name} for a specific capability")
        command_parser.add_argument("--capability", choices=["ocr", "page_elements", "table_structure", "graphic_elements", "rerank"], required=True)
        command_parser.add_argument("--config", help="Optional runtime config JSON")
        command_parser.add_argument("--image-url", action="append")
        command_parser.add_argument("--merge-level", action="append", choices=["word", "sentence", "paragraph"])
        command_parser.add_argument("--confidence-threshold", type=float)
        command_parser.add_argument("--nms-threshold", type=float)
        command_parser.add_argument("--query-text")
        command_parser.add_argument("--passage", action="append")
        command_parser.add_argument("--truncate", choices=["NONE", "END"])
        command_parser.add_argument("--model")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    catalog = load_json(Path(args.catalog))

    if args.command == "plan":
        plan = plan_task(
            args.task_query,
            catalog,
            {
                "image_urls": args.image_url,
                "query_text": args.query_text,
                "passages": args.passage
            }
        )
        print_json(plan)
        return

    runtime = load_runtime_config(args.config)
    request_plan = build_request(args.capability, args, catalog, runtime)
    if args.command == "build-request":
        print_json(request_plan)
        return

    result = invoke_request(request_plan)
    print_json({
        "request": request_plan,
        "result": result
    })


if __name__ == "__main__":
    main()
