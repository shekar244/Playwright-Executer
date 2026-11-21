"""Reading a JSON request body without ever letting a malformed one become an HTML 500."""
from __future__ import annotations

from flask import jsonify, request


def json_object() -> tuple[dict | None, tuple | None]:
    """(body, None) for the current request when its body is a JSON object (an empty body is {}); otherwise
    (None, (a JSON 400 answer, 400)) — for a list, a string, a number, broken JSON or a body that is not
    sent as application/json. Call it first in a POST handler."""
    if not request.get_data(cache=True).strip():
        return {}, None
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, (jsonify({"error": "The request body must be a JSON object (Content-Type: application/json)"}), 400)
    return body, None
