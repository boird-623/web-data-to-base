#!/usr/bin/env python3
"""Persist non-secret semantic operation recipes and verified incident remedies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from state_io import atomic_write_json

DEFAULT_PROFILES = Path.home() / ".config" / "web-data-to-base" / "operation_profiles.local.json"
ALLOWED_STEP_ACTIONS = {
    "navigate", "click", "select", "wait", "extract", "scroll", "switch_frame", "leave_frame", "refresh",
    "fill_account", "fill_secret_from_store", "submit", "trigger_download", "wait_download", "bind_download",
}
LOGIN_ALLOWED_STEP_ACTIONS = {
    "navigate", "click", "select", "wait", "switch_frame", "leave_frame", "refresh",
    "fill_account", "fill_secret_from_store", "submit",
}
LOGIN_VALUE_SOURCES = {
    "fill_account": "account_name",
    "fill_secret_from_store": "system_secret_store",
}
FORBIDDEN_KEY_PARTS = {
    "password", "passwd", "pwd", "cookie", "token", "authorization", "credential",
    "secret_value", "验证码", "密码", "口令", "令牌", "会话", "登录输入值", "dom_snapshot",
    "page_html", "screenshot_data", "storage_state",
}
FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:password|passwd|pwd|cookie|authorization|bearer|token)\s*[:=]"),
    re.compile(r"(?i)document\.cookie|localStorage|sessionStorage"),
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def site_key(site_name: str, login_url: str) -> str:
    host = (urlparse(login_url).hostname or "").casefold()
    identity = f"{site_name.strip().casefold()}\0{host}"
    return "site-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def profile_key(site: str, task: str) -> str:
    return hashlib.sha256(f"{site}\0{task.strip().casefold()}".encode("utf-8")).hexdigest()[:24]


def load(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "profiles": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("profiles", {}), dict):
        raise SystemExit("invalid_operation_profiles_file")
    data.setdefault("schema_version", 1)
    data.setdefault("profiles", {})
    return data


def save(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def read_payload() -> dict:
    payload = json.loads(sys.stdin.read())
    if not isinstance(payload, dict):
        raise SystemExit("payload_must_be_object")
    assert_safe(payload)
    return payload


def assert_safe(value, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                raise SystemExit(f"sensitive_field_forbidden:{path}.{key}")
            assert_safe(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_safe(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) > 4000:
            raise SystemExit(f"oversized_value_forbidden:{path}")
        if any(pattern.search(value) for pattern in FORBIDDEN_VALUE_PATTERNS):
            raise SystemExit(f"sensitive_value_forbidden:{path}")


def ensure_profile(data: dict, site: str, task: str, site_name: str = "", login_url: str = "") -> dict:
    key = profile_key(site, task)
    profiles = data.setdefault("profiles", {})
    profile = profiles.setdefault(key, {
        "site_key": site,
        "site_name": site_name,
        "login_host": urlparse(login_url).hostname or "",
        "task_name": task,
        "version": 1,
        "status": "learning",
        "steps": [],
        "incidents": [],
        "replay": {"success_count": 0, "failure_count": 0, "last_success_at": None, "last_failure_at": None},
        "created_at": now(),
        "updated_at": now(),
    })
    if site_name:
        profile["site_name"] = site_name
    if login_url:
        profile["login_host"] = urlparse(login_url).hostname or ""
    return profile


def validate_step(payload: dict, login_mode: str = "account_password") -> dict:
    action = str(payload.get("action", ""))
    stage = str(payload.get("stage", ""))
    if login_mode == "none" and (stage == "login" or action in {"fill_account", "fill_secret_from_store"}):
        raise SystemExit("public_target_must_not_record_login_steps")
    if action not in ALLOWED_STEP_ACTIONS:
        raise SystemExit("unsupported_or_sensitive_step_action")
    if stage == "login" and action not in LOGIN_ALLOWED_STEP_ACTIONS:
        raise SystemExit("unsupported_login_step_action")
    if action in {"fill_account", "fill_secret_from_store"} and stage != "login":
        raise SystemExit("login_fill_action_requires_login_stage")
    required = ["stage", "action", "target", "success_condition"]
    if any(payload.get(key) in (None, "", []) for key in required):
        raise SystemExit("step_requires_stage_action_target_success_condition")
    if action in LOGIN_VALUE_SOURCES:
        if "value" in payload:
            raise SystemExit("login_fill_step_must_not_store_literal_value")
        if payload.get("value_source") != LOGIN_VALUE_SOURCES[action]:
            raise SystemExit(f"invalid_login_value_source:{action}")
    elif "value_source" in payload:
        raise SystemExit("value_source_only_allowed_for_login_fill_actions")
    allowed = {
        "stage", "action", "target", "value", "value_source", "wait_condition",
        "success_condition", "fallbacks", "notes",
    }
    return {key: payload[key] for key in allowed if key in payload}


def incident_fingerprint(payload: dict) -> str:
    basis = {
        "stage": payload.get("stage"),
        "category": payload.get("category"),
        "url_pattern": payload.get("url_pattern"),
        "frame_signature": payload.get("frame_signature"),
        "symptoms": payload.get("symptoms"),
    }
    return hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def validate_incident(payload: dict) -> dict:
    required = ["stage", "category", "symptoms", "resolution_steps", "verification"]
    if any(payload.get(key) in (None, "", []) for key in required):
        raise SystemExit("incident_requires_stage_category_symptoms_resolution_steps_verification")
    if not isinstance(payload["resolution_steps"], list) or not payload["resolution_steps"]:
        raise SystemExit("resolution_steps_must_be_nonempty_array")
    allowed = {
        "stage", "category", "url_pattern", "page_title_pattern", "frame_signature", "symptoms",
        "failed_attempts", "resolution_steps", "verification", "notes",
    }
    result = {key: payload[key] for key in allowed if key in payload}
    result["fingerprint"] = incident_fingerprint(result)
    return result


def public_profile(profile: dict) -> dict:
    return {
        "exists": True,
        "site_key": profile.get("site_key"),
        "site_name": profile.get("site_name"),
        "task_name": profile.get("task_name"),
        "version": profile.get("version"),
        "status": profile.get("status"),
        "steps": profile.get("steps", []),
        "incidents": profile.get("incidents", []),
        "replay": profile.get("replay", {}),
        "last_revision_note": profile.get("last_revision_note", ""),
    }


def pattern_matches(pattern, actual: str) -> bool:
    if pattern in (None, ""):
        return True
    try:
        return re.search(str(pattern), actual or "", re.IGNORECASE) is not None
    except re.error:
        return str(pattern).casefold() in (actual or "").casefold()


def match_incidents(profile: dict, observation: dict) -> list[dict]:
    assert_safe(observation)
    stage = str(observation.get("stage", ""))
    category = str(observation.get("category", ""))
    url = str(observation.get("url", ""))
    title = str(observation.get("page_title", ""))
    frame = str(observation.get("frame_signature", ""))
    symptoms = " ".join(str(x) for x in observation.get("symptoms", [])) if isinstance(observation.get("symptoms"), list) else str(observation.get("symptoms", ""))
    matches = []
    for incident in profile.get("incidents", []):
        if incident.get("stage") not in (None, "", stage):
            continue
        if incident.get("category") not in (None, "", category):
            continue
        if not pattern_matches(incident.get("url_pattern"), url):
            continue
        if not pattern_matches(incident.get("page_title_pattern"), title):
            continue
        if not pattern_matches(incident.get("frame_signature"), frame):
            continue
        expected_symptoms = incident.get("symptoms", [])
        expected_text = " ".join(str(x) for x in expected_symptoms) if isinstance(expected_symptoms, list) else str(expected_symptoms)
        if expected_text and symptoms and not all(token.casefold() in symptoms.casefold() for token in expected_text.split()[:4]):
            continue
        success = int(incident.get("success_count", 0))
        failure = int(incident.get("failure_count", 0))
        score = success * 10 - failure * 5
        matches.append({**incident, "match_score": score})
    matches.sort(key=lambda item: (item.get("match_score", 0), item.get("last_success_at") or ""), reverse=True)
    return matches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["site-key", "show", "match-incidents", "record-step", "revise-step", "record-incident", "mark-incident", "mark-trial-complete", "mark-replay", "reset-profile"])
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--site-key")
    parser.add_argument("--site-name")
    parser.add_argument("--login-url")
    parser.add_argument("--task-name")
    parser.add_argument("--login-mode", choices=["account_password", "none"], default="account_password")
    parser.add_argument("--fingerprint")
    parser.add_argument("--success", choices=["true", "false"])
    args = parser.parse_args()

    if args.action == "site-key":
        if not args.site_name or not args.login_url:
            raise SystemExit("site_name_and_login_url_required")
        print(json.dumps({"site_key": site_key(args.site_name, args.login_url)}, ensure_ascii=False))
        return 0

    if not args.site_key or not args.task_name:
        raise SystemExit("site_key_and_task_name_required")
    path = args.profiles.expanduser().resolve()
    data = load(path)
    key = profile_key(args.site_key, args.task_name)

    if args.action == "reset-profile":
        removed = data.get("profiles", {}).pop(key, None) is not None
        save(path, data)
        print(json.dumps({"removed": removed}, ensure_ascii=False))
        return 0

    if args.action == "show":
        profile = data.get("profiles", {}).get(key)
        print(json.dumps({"exists": False} if profile is None else public_profile(profile), ensure_ascii=False))
        return 0 if profile is not None else 2

    if args.action == "match-incidents":
        profile = data.get("profiles", {}).get(key)
        if profile is None:
            print(json.dumps({"matches": [], "match_count": 0}, ensure_ascii=False))
            return 0
        matches = match_incidents(profile, read_payload())
        print(json.dumps({"matches": matches, "match_count": len(matches)}, ensure_ascii=False))
        return 0

    profile = ensure_profile(data, args.site_key, args.task_name, args.site_name or "", args.login_url or "")
    if args.action == "mark-incident":
        if not args.fingerprint or args.success is None:
            raise SystemExit("fingerprint_and_success_required")
        incident = next((item for item in profile["incidents"] if item.get("fingerprint") == args.fingerprint), None)
        if incident is None:
            raise SystemExit("incident_not_found")
        if args.success == "true":
            incident["success_count"] = int(incident.get("success_count", 0)) + 1
            incident["last_success_at"] = now()
        else:
            incident["failure_count"] = int(incident.get("failure_count", 0)) + 1
            incident["last_failure_at"] = now()
        profile["updated_at"] = now()
        save(path, data)
        print(json.dumps(public_profile(profile), ensure_ascii=False))
        return 0
    if args.action == "record-step":
        step = validate_step(read_payload(), args.login_mode)
        step["recorded_at"] = now()
        profile["steps"].append(step)
        profile["version"] += 1
    elif args.action == "revise-step":
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise SystemExit("payload_must_be_object")
        new_steps = payload.get("steps")
        if not isinstance(new_steps, list) or not new_steps:
            raise SystemExit("revise_requires_nonempty_steps")
        validated = [validate_step(step, args.login_mode) for step in new_steps]
        steps = profile["steps"]
        index = payload.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            pos = index
        else:
            match = payload.get("match") or {}
            pos = next(
                (
                    i for i, s in enumerate(steps)
                    if (not match.get("stage") or s.get("stage") == match.get("stage"))
                    and (not match.get("target") or s.get("target") == match.get("target"))
                ),
                -1,
            )
        if pos < 0 or pos >= len(steps):
            raise SystemExit("revise_target_step_not_found")
        for step in validated:
            step["recorded_at"] = now()
        steps[pos:pos + 1] = validated
        if payload.get("note"):
            profile["last_revision_note"] = str(payload.get("note"))
        profile["version"] += 1
    elif args.action == "record-incident":
        incident = validate_incident(read_payload())
        existing = next((item for item in profile["incidents"] if item.get("fingerprint") == incident["fingerprint"]), None)
        if existing is None:
            incident.update({"success_count": 1, "failure_count": 0, "last_success_at": now(), "recorded_at": now()})
            profile["incidents"].append(incident)
        else:
            existing.update(incident)
            existing["success_count"] = int(existing.get("success_count", 0)) + 1
            existing["last_success_at"] = now()
        profile["version"] += 1
    elif args.action == "mark-trial-complete":
        if not profile["steps"]:
            raise SystemExit("cannot_complete_trial_without_recorded_steps")
        profile["status"] = "ready"
        profile["trial_completed_at"] = now()
    elif args.action == "mark-replay":
        if args.success is None:
            raise SystemExit("success_required")
        replay = profile.setdefault("replay", {})
        if args.success == "true":
            replay["success_count"] = int(replay.get("success_count", 0)) + 1
            replay["last_success_at"] = now()
        else:
            replay["failure_count"] = int(replay.get("failure_count", 0)) + 1
            replay["last_failure_at"] = now()
            profile["status"] = "needs_repair"
    profile["updated_at"] = now()
    save(path, data)
    print(json.dumps(public_profile(profile), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
