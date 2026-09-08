"""Verify an already-started candidate image before sending it production traffic."""

import argparse
import json
import sys
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed IntraLink rollout check")
    parser.add_argument("--base-url", required=True, help="Candidate API URL, e.g. http://127.0.0.1:8001")
    parser.add_argument("--sha", required=True, help="Expected Git SHA embedded at image build time")
    args = parser.parse_args()
    url = f"{args.base_url.rstrip('/')}/health/rollout?{urlencode({'expected_sha': args.sha})}"
    try:
        with urlopen(url, timeout=10) as response:  # nosec B310: explicit operator-supplied candidate URL
            result = json.load(response)
    except (URLError, OSError, ValueError) as exc:
        print(f"ROLLOUT BLOCKED: readiness endpoint unavailable: {exc}", file=sys.stderr)
        return 2
    if not result.get("ready"):
        print("ROLLOUT BLOCKED: " + "; ".join(result.get("checks") or ["unknown readiness failure"]), file=sys.stderr)
        return 1
    print(f"ROLLOUT READY: API/UI SHA {result['versions']['api_sha']}; security_audit_log present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
