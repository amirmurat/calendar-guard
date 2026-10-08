"""Stateless MCP adapter. No Calendar credentials, storage, or outbound requests."""
import os
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from guard import prepare, check_current, verify, rollback, validate
from schedule_preview import preview_slots

host = os.environ.get('RENDER_EXTERNAL_HOSTNAME') or os.environ.get('VERCEL_PROJECT_PRODUCTION_URL') or os.environ.get('VERCEL_URL', '')
allowed = ['127.0.0.1:*', 'localhost:*']
if host:
    allowed.append(host)
for key in ('VERCEL_URL', 'VERCEL_BRANCH_URL'):
    if os.environ.get(key):
        allowed.append(os.environ[key])
allowed.extend(filter(None, os.environ.get('GUARD_ALLOWED_HOSTS', '').split(',')))
mcp = FastMCP(
    'Calendar Guard',
    instructions=(
        'Validate calendar edits using prepare_block, re-read the event and call '
        'check_current before applying its connector_payload through Google Calendar. '
        'Then re-read and call verify_update. This server never reads or writes calendars. '
        'Provide a complete calendar window and protected intervals. Save the plan '
        'in the existing durable state; supply plan.block as previous_block on later edits. '
        'If validation fails, stop; do not silently bypass it.'
    ),
    host='0.0.0.0', port=int(os.environ.get('PORT', '8000')),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=allowed,
        allowed_origins=['http://127.0.0.1:*', 'http://localhost:*'] + ([f'https://{host}'] if host else []),
    ),
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


@mcp.tool(annotations=READ_ONLY)
def describe_format() -> dict:
    """Return supported schema and limits. No personal data or account access."""
    return {'version': 1, 'required': ['schema_version', 'kind', 'area', 'task', 'flexibility', 'materials', 'conditions', 'deadline', 'notes', 'provenance'],
            'work_required': ['result', 'actions', 'fallback'],
            'kinds': ['work', 'class', 'travel', 'sport', 'meeting', 'practice'],
            'unknown': 'Use null or deadline.kind=unknown; never invent facts.',
            'limits': ['Existing timed events only', 'No events with guests', 'Single occurrence only', 'Caller supplies complete context', 'No Google Calendar access or persistent storage']}


@mcp.tool(annotations=READ_ONLY)
def validate_block(block: dict) -> dict:
    """Validate fields without scheduling. Additional fields are preserved."""
    return {'ok': True, 'block': validate(block)}


@mcp.tool(annotations=READ_ONLY)
def prepare_block(request: dict) -> dict:
    """Prepare a minimal event update. request needs event, block, context and optional previous_block/time. Does not write Calendar."""
    return prepare(request)


@mcp.tool(annotations=READ_ONLY)
def check_current_event(plan: dict, current: dict) -> dict:
    """Reject stale event snapshots. Current must come from a new Calendar read."""
    return check_current(plan, current)


@mcp.tool(annotations=READ_ONLY)
def verify_update(plan: dict, current: dict) -> dict:
    """Verify actual Calendar result after writing; detect unintended changes."""
    return verify(plan, current)


@mcp.tool(annotations=READ_ONLY)
def prepare_rollback(plan: dict, current: dict) -> dict:
    """Prepare an inverse patch only if no subsequent edits occurred. Recheck scheduling before applying."""
    return rollback(plan, current)



@mcp.tool(annotations=READ_ONLY)
def preview_schedule(request: dict) -> dict:
    """Suggest up to 3 slots from fresh complete Calendar data and explicit deadlines.

    Strictly read-only. Caller supplies protected windows from Planner rules.
    An accepted suggestion is never a Calendar update or evidence of task completion.
    """
    return preview_slots(request)


@mcp.custom_route('/health', methods=['GET'])
async def health(request):
    return JSONResponse({'status': 'ok', 'service': 'calendar-guard', 'schema': 1})


# ASGI entrypoint used by Vercel's Python runtime.
app = mcp.streamable_http_app()

if __name__ == '__main__':
    # Never log calendar request/response bodies.
    mcp.run(transport='streamable-http')
