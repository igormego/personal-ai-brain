"""One explicitly approved, local-only, single-paid-call Brain experiment.

Run in trusted Linux/WSL shell. This script never accesses GitHub, invokes
capability handlers or stores messages. It prints a *decision plan*, not a
backend result. The API credential is read from a protected file, not stdin.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_decision import DecisionRejected, decide
from personal_ai_brain.assistant_decision_context import build_decision_context
from personal_ai_brain.assistant_openai_provider import (
    MODEL, ModelUnavailable, OpenAIDecisionProvider,
)
from personal_ai_brain.assistant_project_os import validate_selector
from personal_ai_brain.control import Capability, Registry, no_arguments
from personal_ai_brain.core_agent import CoreSnapshot, StateRecord


def fixture_catalog():
    """Synthetic IDs/names only; NOT live operational or source-of-truth state."""
    today = date.today()
    records = []
    for project_id, name, scope, path in (
        ("homelab", "Homelab & Media Stack", "project", ".project/state.yaml"),
        ("homelab", "Media Automation", "workstream",
         ".project/workstreams/media-automation.yaml"),
        ("recipe-intelligence", "Recipe Intelligence", "project", ".project/state.yaml"),
    ):
        records.append(StateRecord(
            project_id=project_id, project_name=name, scope=scope,
            repository="fixture/unused", path=path, attention="none",
            status="unknown", phase="fixture", last_verified="",
            sync_status="unknown", current_gate="", exact_next_action="",
        ))
    return build_decision_context(
        CoreSnapshot(generated_on=today.isoformat(), records=records), today=today,
    )


def fixture_registry():
    def not_executable(_arguments):
        raise AssertionError("Provider experiment may never execute capabilities")
    return Registry([
        Capability("project_os.status", "Bounded Project OS status", "read_only",
                   {}, validate_selector, not_executable),
        Capability("core.health", "Core read-only health", "read_only",
                   {}, no_arguments, not_executable),
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description="One opt-in paid model decision; no tools")
    parser.add_argument("--key-file", type=Path, required=True,
                        help="Protected Linux/WSL file (owner, mode 0600/0400); do not paste key")
    parser.add_argument("--allow-paid-call", action="store_true",
                        help="Explicitly authorize one external API call")
    args = parser.parse_args(argv)
    if not args.allow_paid_call:
        parser.error("A paid API call requires --allow-paid-call")
    try:
        # No user text in shell history or process args; avoid sensitive content.
        print("Enter one NON-SENSITIVE Slovak or English test request:", file=sys.stderr)
        message = input()
        now = datetime.now(timezone.utc)
        request = normalize_request(
            {"schema_version": "0.1", "request_id": str(uuid4()),
             "message": message, "created_at": now.isoformat()},
            trusted_user_id="operator", trusted_surface="chatgpt",
            trusted_reply_target="manual.acceptance", now=now,
        )
        provider = OpenAIDecisionProvider(args.key_file, allow_paid_call=True)
        started = time.monotonic()
        plan = decide(request, fixture_registry(), provider, context=fixture_catalog())
        elapsed_ms = round((time.monotonic() - started) * 1000)
        usage = provider.usage
        print(json.dumps({
            "model": MODEL, "status": "decision_only_not_executed",
            "elapsed_ms": elapsed_ms,
            "action": plan.action, "capability": plan.capability,
            "arguments": plan.arguments,
            "usage": {"input_tokens": usage.input_tokens,
                      "output_tokens": usage.output_tokens} if usage else None,
            "estimated_usd_2026_10_29_standard_rates": round(
                (usage.input_tokens * 0.10 + usage.output_tokens * 0.50) / 1_000_000, 8
            ) if usage else None,
        }, ensure_ascii=False))
        return 0
    except (DecisionRejected, ModelUnavailable) as error:
        # Safe codes only; planner maps provider failures to provider_unavailable.
        code = str(error)
        if code not in {
            "provider_unavailable", "context_unavailable", "decision_denied",
            "model_decision_invalid", "trusted_input_required",
            "model_call_not_authorized", "model_auth_unavailable",
        }:
            code = "manual_acceptance_unavailable"
        print(json.dumps({"status": "failed", "code": code}), file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt, ValueError):
        print('{"status":"failed","code":"manual_input_unavailable"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
