"""Public local CLI for selection, independent generation, validation and migration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .composition import ProductBundle, validate_product
from .storage import save_product
from .tools import call_domain_tool


def run(args: argparse.Namespace) -> int:
    """Run one JSON operation and use revision-aware persistence for product bundles."""
    payload = json.loads(sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8"))
    result = call_domain_tool(args.operation, payload)
    if args.output and result.get("format") == "forma.product-domains":
        save_product(ProductBundle.model_validate(result), args.output, expected_sha256=args.expected_sha256)
    else:
        content = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            Path(args.output).write_text(content, encoding="utf-8")
        else:
            sys.stdout.write(content)
    if args.operation == "generate":
        return 0 if validate_product(ProductBundle.model_validate(result)).is_valid else 1
    if args.operation.startswith("validate") or (args.operation == "bindings" and payload.get("action") in {"validate", "validate-product"}):
        return 0 if result["is_valid"] else 1
    if args.operation == "export-hardware":
        return 0 if result["payload"] is not None else 1
    return 0


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the additive domains command while leaving legacy commands compatible."""
    parser = subparsers.add_parser("domains", help="Plan, author and compose independent domain IRs.")
    parser.add_argument("operation", choices=["plan", "generate", "compose", "registry", "validate", "validate-domain", "schema",
                                              "import-hardware", "export-hardware", "bindings"])
    parser.add_argument("input", help="Input JSON path, or - for stdin.")
    parser.add_argument("--output", help="Output JSON path; product bundles retain immutable revisions.")
    parser.add_argument("--expected-sha256", help="Required current content digest when replacing a product bundle.")
    parser.set_defaults(func=run)
