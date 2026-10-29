"""One bounded two-turn conversational Brain v0.5 API demo with FIXTURE data.

This is NOT a Gateway, GitHub or real Project OS client. It consumes the
protected existing Linux/WSL API key and requires explicit paid-call opt-in.
Each user turn permits at most 3 model requests. No history is persisted.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_conversation import ConversationRejected, run_conversation
from personal_ai_brain.assistant_decision_context import build_decision_context
from personal_ai_brain.assistant_openai_conversation import OpenAIConversationProvider
from personal_ai_brain.assistant_openai_provider import MODEL
from personal_ai_brain.assistant_project_os import answer_project_status, validate_selector
from personal_ai_brain.control import Capability, Registry
from personal_ai_brain.core_agent import CoreSnapshot, StateRecord


def fixture_snapshot():
    today = date.today().isoformat()
    records = []
    for pid, label in (
        ("homelab", "Homelab & Media Stack"),
        ("recipe-intelligence", "Recipe Intelligence"),
    ):
        records.append(StateRecord(
            project_id=pid, project_name=label, scope="project",
            repository="fixture/not-a-real-repository", path=".project/state.yaml",
            status="fixture-only", phase="demo-only", attention="none",
            last_verified="", sync_status="unknown",
            current_gate="This is synthetic demonstration data, not live Project OS.",
            exact_next_action="Connect the real authenticated Gateway to get actual state.",
        ))
    return CoreSnapshot(generated_on=today, records=records)


def fixture_registry():
    def forbidden(_selector):
        raise AssertionError("Brain must never run the Registry handler directly")
    return Registry([
        Capability("project_os.status", "Read project state", "read_only",
                   {}, validate_selector, forbidden),
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Two paid but synthetic-data Brain demo turns")
    parser.add_argument("--key-file", required=True, type=Path,
                        help="Owner-only Linux/WSL API key file, not key value")
    parser.add_argument("--allow-paid-call", action="store_true",
                        help="Deliberately authorize up to 3 API requests per user turn")
    args = parser.parse_args(argv)
    if not args.allow_paid_call:
        parser.error("Specify --allow-paid-call to enable paid API requests")

    snapshot = fixture_snapshot()
    context = build_decision_context(snapshot)
    registry = fixture_registry()
    history = ()
    print("FIXTURE DEMO: No real Project OS status, no backend operations, no persistence.",
          file=sys.stderr)
    print("Enter a NON-SENSITIVE Slovak question; max TWO turns.", file=sys.stderr)

    for turn in range(2):
        try:
            message = input(f"Otázka {turn+1}/2: ").strip()
            if not message:
                break
            now = datetime.now(timezone.utc)
            request = normalize_request(
                {"schema_version": "0.1", "request_id": str(uuid4()),
                 "message": message, "created_at": now.isoformat()},
                trusted_user_id="operator", trusted_surface="chatgpt",
                trusted_reply_target="local.demo", now=now,
            )
            def read_only(req, selector):
                return answer_project_status(req, snapshot, selector)
            provider = OpenAIConversationProvider(args.key_file, allow_paid_call=True)
            reply = run_conversation(
                request, registry, context, provider, read_only, history=history,
            )
            history = reply.history
            print("\nBrain: " + reply.text)
            print(json.dumps({
                "model": MODEL, "status": "synthetic_source_only",
                "model_steps": reply.model_steps, "tool_calls": reply.tool_calls,
                "input_tokens": provider.total_input_tokens,
                "output_tokens": provider.total_output_tokens,
            }, ensure_ascii=False))
        except (ConversationRejected, ValueError, EOFError, KeyboardInterrupt):
            print('{"status":"failed","code":"demo_unavailable"}', file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
