"""Compatibility dispatcher for a read-only preview through an older MCP tool registry.

No Calendar access. The caller supplies all bounded, fresh context. The
default prepare_block contract is unchanged; only the explicit preview mode
takes this alternate path.
"""
from guard import GuardError, prepare, prepare_execution
from schedule_preview import preview_slots


def dispatch_prepare(request, *, now=None):
    if not isinstance(request, dict) or "operation" not in request:
        return prepare(request)
    if request.get("operation") == "execution_status":
        return prepare_execution(request)
    if request.get("operation") != "preview_schedule":
        raise GuardError("Unknown prepare_block operation")
    if set(request) != {"operation", "preview_request"}:
        raise GuardError("Preview operation requires exactly operation + preview_request")
    return preview_slots(request["preview_request"], now=now)
