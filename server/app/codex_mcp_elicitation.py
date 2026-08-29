from __future__ import annotations

import base64
import math
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from app.agent_runtime import AgentRuntimeError

MCP_ELICITATION_METHOD = "mcpServer/elicitation/request"
_MCP_ACTION_ID = "__fdex_mcp_action__"
_MCP_FIELD_PREFIX = "__fdex_mcp_field__"
_SUPPORTED_ACTIONS = {"accept", "decline", "cancel"}
_OPENAI_FORM_MODES = {"openai/form", "openaiForm"}
_MAX_URL = 4096
_MAX_TEXT = 12000
_MAX_FIELDS = 100
_MAX_OPTIONS = 200


def install_mcp_elicitation_compat() -> None:
    """Extend the Phase 7.23 generic interaction bridge without forking its transport/store."""
    import app.codex_interaction_store as store_module
    import app.codex_interactions as interactions_module

    store_module._SUPPORTED.add(MCP_ELICITATION_METHOD)
    interactions_module._SUPPORTED.add(MCP_ELICITATION_METHOD)

    if getattr(interactions_module, "_fdex_mcp_elicitation_installed", False):
        return
    original_kind = interactions_module.interaction_kind
    original_public_event = interactions_module._public_event

    def interaction_kind(method: str) -> str:
        if method == MCP_ELICITATION_METHOD:
            return "mcp_elicitation"
        return original_kind(method)

    def public_event(row: dict[str, Any]) -> dict[str, Any]:
        event = original_public_event(row)
        if str(row.get("method") or "") != MCP_ELICITATION_METHOD:
            return event
        projected = decorate_mcp_interaction(row)
        event["protocolMethod"] = MCP_ELICITATION_METHOD
        event["kind"] = "mcp_elicitation"
        event["method"] = str(projected.get("method") or MCP_ELICITATION_METHOD)
        event["request"] = projected.get("request") if isinstance(projected.get("request"), dict) else {}
        return event

    interactions_module.interaction_kind = interaction_kind
    interactions_module._public_event = public_event
    interactions_module._fdex_mcp_elicitation_installed = True


def _field_token(name: str) -> str:
    encoded = base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{_MCP_FIELD_PREFIX}{encoded}"


def _request(row: dict[str, Any]) -> dict[str, Any]:
    request = row.get("request")
    return request if isinstance(request, dict) else {}


def _mode(request: dict[str, Any]) -> str:
    return str(request.get("mode") or "").strip()


def _server_name(request: dict[str, Any]) -> str:
    return str(request.get("serverName") or "").strip()[:240]


def safe_https_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or len(raw) > _MAX_URL or any(ord(ch) < 32 for ch in raw):
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return raw


def _enum_options(schema: dict[str, Any]) -> tuple[list[tuple[str, str]], bool]:
    """Return (value,title) options and whether the schema is multi-select."""
    schema_type = str(schema.get("type") or "")
    if schema_type == "string":
        values = schema.get("enum")
        if isinstance(values, list):
            clean = [str(value) for value in values[:_MAX_OPTIONS]]
            names = schema.get("enumNames")
            titles = [str(value) for value in names[: len(clean)]] if isinstance(names, list) else []
            return [(value, titles[index] if index < len(titles) else value) for index, value in enumerate(clean)], False
        one_of = schema.get("oneOf")
        if isinstance(one_of, list):
            result: list[tuple[str, str]] = []
            for option in one_of[:_MAX_OPTIONS]:
                if not isinstance(option, dict) or "const" not in option:
                    continue
                value = str(option.get("const") or "")
                result.append((value, str(option.get("title") or value)))
            return result, False
        return [], False
    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            return [], True
        values = items.get("enum")
        if isinstance(values, list):
            return [(str(value), str(value)) for value in values[:_MAX_OPTIONS]], True
        any_of = items.get("anyOf")
        if isinstance(any_of, list):
            result: list[tuple[str, str]] = []
            for option in any_of[:_MAX_OPTIONS]:
                if not isinstance(option, dict) or "const" not in option:
                    continue
                value = str(option.get("const") or "")
                result.append((value, str(option.get("title") or value)))
            return result, True
        return [], True
    return [], False


def _schema_parts(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], set[str]]:
    schema = request.get("requestedSchema")
    if not isinstance(schema, dict) or str(schema.get("type") or "") != "object":
        raise AgentRuntimeError("MCP form requestedSchema must be an object schema")
    properties_raw = schema.get("properties")
    if not isinstance(properties_raw, dict) or len(properties_raw) > _MAX_FIELDS:
        raise AgentRuntimeError("MCP form properties are invalid or exceed the FDEX field limit")
    properties: dict[str, dict[str, Any]] = {}
    for name, field in properties_raw.items():
        clean_name = str(name)
        if not clean_name or len(clean_name) > 240 or not isinstance(field, dict):
            raise AgentRuntimeError("MCP form contains an invalid field definition")
        properties[clean_name] = field
    required_raw = schema.get("required")
    required = {str(value) for value in required_raw} if isinstance(required_raw, list) else set()
    if not required.issubset(properties):
        raise AgentRuntimeError("MCP form required list references an unknown field")
    return schema, properties, required


def _question_for_field(name: str, schema: dict[str, Any], required: bool, server_name: str) -> dict[str, Any]:
    field_type = str(schema.get("type") or "")
    if field_type not in {"string", "number", "integer", "boolean", "array"}:
        raise AgentRuntimeError(f"FDEX does not support MCP elicitation field type: {field_type or 'unknown'}")
    options, multi = _enum_options(schema)
    if field_type == "array" and not options:
        raise AgentRuntimeError("FDEX only supports enum-backed MCP array fields")
    if field_type == "boolean":
        options = [("true", "true"), ("false", "false")]
    title = str(schema.get("title") or name)[:500]
    description = str(schema.get("description") or "").strip()
    default = schema.get("default")
    prompt_parts = [description or f"MCP server {server_name or '-'} requests `{name}`."]
    prompt_parts.append("Required." if required else "Optional.")
    if default is not None:
        prompt_parts.append(f"Default: {default}")
    if multi:
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if min_items is not None or max_items is not None:
            prompt_parts.append(
                f"Selections: min={min_items if min_items is not None else 0}, "
                f"max={max_items if max_items is not None else 'unbounded'}"
            )
    return {
        "id": _field_token(name),
        "header": title,
        "question": " ".join(prompt_parts)[:12000],
        "isOther": False,
        "isSecret": False,
        "options": [
            {"label": value, "description": option_title if option_title != value else ""}
            for value, option_title in options
        ] or None,
    }


def _action_question(message: str, *, allow_accept: bool) -> dict[str, Any]:
    actions = ["accept", "decline", "cancel"] if allow_accept else ["decline", "cancel"]
    descriptions = {
        "accept": "Continue and send the validated MCP response.",
        "decline": "Decline this MCP elicitation and continue when the server supports it.",
        "cancel": "Cancel this MCP elicitation.",
    }
    return {
        "id": _MCP_ACTION_ID,
        "header": "MCP action",
        "question": (message or "Choose how FDEX should resolve this MCP elicitation.")[:12000],
        "isOther": False,
        "isSecret": False,
        "options": [{"label": action, "description": descriptions[action]} for action in actions],
    }


def decorate_mcp_interaction(row: dict[str, Any]) -> dict[str, Any]:
    """Project MCP elicitation onto Phase 7.23's existing safe requestUserInput UI."""
    if str(row.get("method") or "") != MCP_ELICITATION_METHOD:
        return dict(row)
    result = dict(row)
    request = _request(row)
    mode = _mode(request)
    server_name = _server_name(request)
    message = str(request.get("message") or "")
    questions: list[dict[str, Any]] = []
    allow_accept = False

    if mode == "form":
        try:
            _schema, properties, required = _schema_parts(request)
            _validate_schema_defaults(properties)
            questions.append(_action_question(message, allow_accept=True))
            for name, schema in properties.items():
                questions.append(_question_for_field(name, schema, name in required, server_name))
            allow_accept = True
        except AgentRuntimeError as exc:
            questions.append(
                _action_question(
                    f"{message}\n\nFDEX cannot safely render this MCP schema: {exc}",
                    allow_accept=False,
                )
            )
    elif mode == "url":
        safe_url = safe_https_url(request.get("url"))
        proprietary = server_name == "codex_apps"
        if safe_url and not proprietary:
            host = urlsplit(safe_url).hostname or ""
            questions.append(
                _action_question(
                    f"{message}\n\nOpen this HTTPS URL in your browser, complete the external action, then choose accept.\nHost: {host}\nURL: {safe_url}",
                    allow_accept=True,
                )
            )
            allow_accept = True
        else:
            reason = (
                "FDEX does not proxy ChatGPT connector authentication from Codex Runtime."
                if proprietary
                else "The URL is not a valid credential-free HTTPS URL."
            )
            questions.append(_action_question(f"{message}\n\n{reason}", allow_accept=False))
    elif mode in _OPENAI_FORM_MODES:
        questions.append(
            _action_question(
                f"{message}\n\nThis is an OpenAI-specific elicitation schema. FDEX only accepts the public MCP form schema in this phase.",
                allow_accept=False,
            )
        )
    else:
        questions.append(
            _action_question(
                f"{message}\n\nUnsupported MCP elicitation mode: {mode or '-'}",
                allow_accept=False,
            )
        )

    result["protocol_method"] = MCP_ELICITATION_METHOD
    result["mcp_mode"] = mode
    result["mcp_server_name"] = server_name
    result["mcp_accept_supported"] = allow_accept
    result["method"] = "item/tool/requestUserInput"
    result["request"] = {
        "threadId": str(request.get("threadId") or ""),
        "turnId": str(request.get("turnId") or ""),
        "itemId": str(request.get("elicitationId") or f"mcp:{server_name}"),
        "isBlocking": True,
        "questions": questions,
    }
    return result


def _single(values: list[str], field: str) -> str:
    clean = [str(value) for value in values if str(value) != ""]
    if len(clean) != 1:
        raise AgentRuntimeError(f"MCP field `{field}` requires exactly one value")
    if len(clean[0]) > _MAX_TEXT:
        raise AgentRuntimeError(f"MCP field `{field}` exceeds the FDEX value limit")
    return clean[0]


def _validate_string(value: str, schema: dict[str, Any], field: str) -> str:
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if isinstance(min_length, int) and len(value) < min_length:
        raise AgentRuntimeError(f"MCP field `{field}` is shorter than minLength")
    if isinstance(max_length, int) and len(value) > max_length:
        raise AgentRuntimeError(f"MCP field `{field}` exceeds maxLength")
    options, _multi = _enum_options(schema)
    if options and value not in {option[0] for option in options}:
        raise AgentRuntimeError(f"MCP field `{field}` is not an allowed enum value")
    fmt = str(schema.get("format") or "")
    if fmt == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise AgentRuntimeError(f"MCP field `{field}` is not a valid email")
    if fmt == "uri" and urlsplit(value).scheme == "":
        raise AgentRuntimeError(f"MCP field `{field}` is not a valid URI")
    if fmt == "date":
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise AgentRuntimeError(f"MCP field `{field}` is not a valid ISO date") from exc
    if fmt == "date-time":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AgentRuntimeError(f"MCP field `{field}` is not a valid ISO date-time") from exc
    return value


def _validate_number(value: str, schema: dict[str, Any], field: str) -> int | float:
    field_type = str(schema.get("type") or "")
    try:
        parsed: int | float = int(value) if field_type == "integer" else float(value)
    except ValueError as exc:
        raise AgentRuntimeError(f"MCP field `{field}` must be {field_type}") from exc
    if isinstance(parsed, float) and not math.isfinite(parsed):
        raise AgentRuntimeError(f"MCP field `{field}` must be finite")
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, (int, float)) and parsed < minimum:
        raise AgentRuntimeError(f"MCP field `{field}` is below minimum")
    if isinstance(maximum, (int, float)) and parsed > maximum:
        raise AgentRuntimeError(f"MCP field `{field}` exceeds maximum")
    return parsed


def _validate_array(values: list[str], schema: dict[str, Any], field: str) -> list[str]:
    clean = [str(value) for value in values if str(value) != ""]
    if len(clean) != len(set(clean)):
        raise AgentRuntimeError(f"MCP field `{field}` contains duplicate selections")
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if isinstance(min_items, int) and len(clean) < min_items:
        raise AgentRuntimeError(f"MCP field `{field}` has fewer than minItems selections")
    if isinstance(max_items, int) and len(clean) > max_items:
        raise AgentRuntimeError(f"MCP field `{field}` exceeds maxItems selections")
    options, _multi = _enum_options(schema)
    allowed = {option[0] for option in options}
    if not allowed or any(value not in allowed for value in clean):
        raise AgentRuntimeError(f"MCP field `{field}` contains a value outside the allowed enum")
    return clean


def _validate_default(default: Any, schema: dict[str, Any], field: str) -> Any:
    field_type = str(schema.get("type") or "")
    if field_type == "string":
        if not isinstance(default, str):
            raise AgentRuntimeError(f"MCP field `{field}` has an invalid string default")
        return _validate_string(default, schema, field)
    if field_type == "integer":
        if isinstance(default, bool) or not isinstance(default, int):
            raise AgentRuntimeError(f"MCP field `{field}` has an invalid integer default")
        return _validate_number(str(default), schema, field)
    if field_type == "number":
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            raise AgentRuntimeError(f"MCP field `{field}` has an invalid number default")
        return _validate_number(str(default), schema, field)
    if field_type == "boolean":
        if not isinstance(default, bool):
            raise AgentRuntimeError(f"MCP field `{field}` has an invalid boolean default")
        return default
    if field_type == "array":
        if not isinstance(default, list) or any(not isinstance(value, str) for value in default):
            raise AgentRuntimeError(f"MCP field `{field}` has an invalid array default")
        return _validate_array(default, schema, field)
    raise AgentRuntimeError(f"FDEX does not support MCP elicitation field type: {field_type or 'unknown'}")


def _validate_schema_defaults(properties: dict[str, dict[str, Any]]) -> None:
    for name, schema in properties.items():
        if "default" in schema:
            _validate_default(schema.get("default"), schema, name)


def _form_content(request: dict[str, Any], values: dict[str, list[str]]) -> tuple[dict[str, Any], list[str]]:
    _schema, properties, required = _schema_parts(request)
    # Validate server-authored schema defaults before looking at user values. This makes a broken
    # or malicious MCP schema deterministically fail on its own defect instead of depending on
    # which user field happens to be checked first.
    _validate_schema_defaults(properties)
    token_to_name = {_field_token(name): name for name in properties}
    unknown = sorted(set(values) - set(token_to_name) - {_MCP_ACTION_ID})
    if unknown:
        raise AgentRuntimeError("MCP elicitation contained values for unknown fields")
    content: dict[str, Any] = {}
    for token, name in token_to_name.items():
        schema = properties[name]
        raw_values = [str(value) for value in values.get(token, []) if str(value) != ""]
        if not raw_values:
            if "default" in schema:
                content[name] = _validate_default(schema.get("default"), schema, name)
                continue
            if name in required:
                raise AgentRuntimeError(f"MCP field `{name}` is required")
            continue
        field_type = str(schema.get("type") or "")
        if field_type == "string":
            content[name] = _validate_string(_single(raw_values, name), schema, name)
        elif field_type in {"number", "integer"}:
            content[name] = _validate_number(_single(raw_values, name), schema, name)
        elif field_type == "boolean":
            raw = _single(raw_values, name).lower()
            if raw not in {"true", "false"}:
                raise AgentRuntimeError(f"MCP field `{name}` must be true or false")
            content[name] = raw == "true"
        elif field_type == "array":
            content[name] = _validate_array(raw_values, schema, name)
        else:
            raise AgentRuntimeError(f"FDEX does not support MCP elicitation field type: {field_type or 'unknown'}")
    return content, sorted(content)


def mcp_elicitation_response(
    row: dict[str, Any],
    values: dict[str, list[str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(row.get("method") or "") != MCP_ELICITATION_METHOD:
        raise AgentRuntimeError("interaction is not an MCP elicitation")
    request = _request(row)
    mode = _mode(request)
    server_name = _server_name(request)
    action = _single(values.get(_MCP_ACTION_ID, []), "MCP action").strip().lower()
    if action not in _SUPPORTED_ACTIONS:
        raise AgentRuntimeError("invalid MCP elicitation action")

    if action in {"decline", "cancel"}:
        return (
            {"action": action, "content": None, "_meta": None},
            {"action": action, "mode": mode, "serverName": server_name},
        )

    if mode in _OPENAI_FORM_MODES:
        raise AgentRuntimeError("FDEX does not accept OpenAI-specific MCP form modes")
    if mode == "url":
        if server_name == "codex_apps":
            raise AgentRuntimeError("FDEX does not proxy ChatGPT connector authentication from Codex Runtime")
        safe_url = safe_https_url(request.get("url"))
        if safe_url is None:
            raise AgentRuntimeError("MCP URL elicitation is not a valid credential-free HTTPS URL")
        return (
            {"action": "accept", "content": None, "_meta": None},
            {
                "action": "accept",
                "mode": "url",
                "serverName": server_name,
                "externalHost": urlsplit(safe_url).hostname or "",
            },
        )
    if mode != "form":
        raise AgentRuntimeError("unsupported MCP elicitation mode")

    content, field_names = _form_content(request, values)
    return (
        {"action": "accept", "content": content, "_meta": None},
        {
            "action": "accept",
            "mode": "form",
            "serverName": server_name,
            "fieldNames": field_names,
            "fieldCount": len(field_names),
        },
    )
