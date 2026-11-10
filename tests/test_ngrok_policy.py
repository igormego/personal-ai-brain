"""Static guard on the narrow ngrok agent-endpoint ingress policy.

This tests YAML/scope, not provider-side CEL evaluation or network acceptance.
"""
from pathlib import Path

import yaml


def test_ngrok_webhook_policy_only_allows_exact_get_post_path():
    policy_path = Path("config/ngrok-whatsapp-traffic-policy.yml")
    config = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    assert config == {
        "on_http_request": [{
            "expressions": ["req.url.path != '/webhook' || !(req.method in ['GET', 'POST'])"],
            "actions": [{"type": "deny", "config": {"status_code": 404}}],
        }],
    }
