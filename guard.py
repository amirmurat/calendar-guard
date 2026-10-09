"""Calendar Guard v1. Python 3.11+, standard library only; no credentials needed.

Prepare -> re-read/check -> connector write -> verify. Never writes remotely itself.
All command input/output is JSON. See README.md for the connector workflow.
"""
import argparse
import base64
import copy
import hashlib
import json
import sys
import re
from html.parser import HTMLParser
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

VERSION = 1
OPEN = '[AMIR-CALENDAR-GUARD:v1]'
CLOSE = '[/AMIR-CALENDAR-GUARD]'
KINDS = {'work', 'class', 'travel', 'sport', 'meeting', 'practice'}
LABELS = {'result': 'Результат', 'actions': 'Действия', 'materials': 'Материалы',
          'conditions': 'Условия', 'deadline': 'Срок', 'flexibility': 'Гибкость',
          'fallback': 'Если не успел', 'notes': 'Важные примечания', 'provenance': 'Основание'}


class GuardError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise GuardError(message)


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def plain(text):
    class Reader(HTMLParser):
        def __init__(self):
            super().__init__(); self.parts = []
        def handle_starttag(self, tag, attrs):
            if tag in ('p', 'div', 'br', 'li'): self.parts.append('\n')
        def handle_endtag(self, tag):
            if tag in ('p', 'div', 'li'): self.parts.append('\n')
        def handle_data(self, data): self.parts.append(data)
    reader = Reader(); reader.feed(text or '')
    value = ''.join(reader.parts)
    value = re.sub(r'\\([\\`*_{}\[\]()#+\-.!>])', r'\1', value)
    return ' '.join(value.split())


def merge(old, patch):
    """Deep merge, preserving unmentioned extension fields. Null is explicit data."""
    out = copy.deepcopy(old)
    for key, value in patch.items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else copy.deepcopy(value)
    return out


def validate(block):
    require(isinstance(block, dict), 'block должен быть объектом')
    require(block.get('schema_version') == VERSION, 'Неизвестная версия схемы; нужна явная миграция')
    require(block.get('kind') in KINDS, 'Неизвестный тип блока')
    for key in ('area', 'task'):
        require(isinstance(block.get(key), str) and bool(block[key].strip()), f'Не заполнено {key}')
    require(isinstance(block.get('flexibility'), dict), 'Нужны правила гибкости')
    require(type(block['flexibility'].get('movable')) is bool, 'movable должен быть true/false')
    if block['kind'] == 'work':
        require(isinstance(block.get('result'), str) and bool(block['result'].strip()), 'Для работы нужен результат')
        require(isinstance(block.get('actions'), list) and bool(block['actions']) and all(isinstance(a, str) and a.strip() for a in block['actions']), 'Нужен хотя бы один конкретный шаг')
        require(isinstance(block.get('fallback'), str) and bool(block['fallback'].strip()), 'Нужно действие при нехватке времени')
    for key in ('materials', 'conditions', 'deadline', 'notes', 'provenance'):
        require(key in block, f'Отсутствует {key}; укажи null, если неизвестно')
    deadline = block['deadline']
    if deadline is not None:
        require(isinstance(deadline, dict) and deadline.get('kind') in ('confirmed', 'internal', 'unknown'), 'Укажи тип срока: confirmed/internal/unknown')
        if deadline['kind'] == 'confirmed':
            require(bool(deadline.get('source')), 'Подтверждённому сроку нужен источник')
    for key in ('min_minutes', 'max_minutes'):
        if key in block['flexibility']:
            require(type(block['flexibility'][key]) is int and block['flexibility'][key] > 0, f'Некорректно {key}')
    lo, hi = (block['flexibility'].get(k) for k in ('min_minutes', 'max_minutes'))
    require(not (lo and hi and lo > hi), 'min_minutes больше max_minutes')
    require(OPEN not in canonical(block) and CLOSE not in canonical(block), 'Зарезервированный маркер в содержимом')
    return block


def display(value):
    if value is None:
        return 'Не подтверждено / не задано'
    if isinstance(value, list):
        return '\n'.join('- ' + display(v) for v in value) or 'Не требуется'
    if isinstance(value, dict):
        names = {'movable': 'Можно переносить', 'splittable': 'Можно делить', 'min_minutes': 'Минимум минут', 'max_minutes': 'Максимум минут', 'kind': 'Тип', 'value': 'Значение', 'source': 'Источник', 'not_before': 'Не раньше', 'not_after': 'Не позже'}
        return '; '.join(names.get(k, k) + ': ' + display(value[k]) for k in sorted(value))
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    return {'confirmed': 'подтверждённый', 'internal': 'внутренний ориентир', 'unknown': 'не подтверждён'}.get(str(value), str(value))


def section(block):
    validate(block)
    lines = [OPEN]
    for key, label in LABELS.items():
        if key in block:
            lines.append(label + ': ' + display(block[key]))
    extensions = {k: v for k, v in block.items() if k not in set(LABELS) | {'schema_version', 'kind', 'area', 'task', 'task_id'}}
    if extensions:
        lines.append('Дополнительные сведения: ' + display(extensions))
    return '\n\n'.join(lines) + '\n\n' + CLOSE


def extract(description, previous_block=None):
    description = description or ''
    if OPEN not in description:
        require('[AMIR-CALENDAR-GUARD:' not in description and CLOSE not in description, 'Неизвестный/повреждённый управляемый раздел')
        return None, None
    require(description.count(OPEN) == 1 and description.count(CLOSE) == 1, 'Дублированные/повреждённые маркеры')
    start, end = description.index(OPEN), description.index(CLOSE) + len(CLOSE)
    require(end > start, 'Некорректный порядок маркеров')
    text = description[start:end]
    require(previous_block is not None, 'Нужна сохранённая предыдущая структура из plan.json; не восстанавливай её догадкой')
    block = previous_block
    require(plain(section(block)) == plain(text), 'Управляемая часть вручную изменена; сначала перенеси изменения в данные, ничего не затирай')
    return block, (start, end)


def stamp(value):
    if isinstance(value, dict):
        value = value.get('dateTime')
    require(isinstance(value, str), 'Поддерживаются только события с точным временем; all-day требует отдельной обработки')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise GuardError('Неверное время ISO-8601') from exc
    require(result.tzinfo is not None, 'Часовой пояс обязателен')
    return result


def check_schedule(event, block, context):
    require(context.get('complete') is True, 'Контекст календаря должен быть полным, со всеми страницами')
    start, end = stamp(event['start']), stamp(event['end'])
    require(end > start, 'Конец должен быть позже начала')
    require(stamp(context['window_start']) <= start and end <= stamp(context['window_end']), 'Событие вне прочитанного окна')
    duration = (end - start).total_seconds() / 60
    flex = block['flexibility']
    require(duration >= flex.get('min_minutes', 0), 'Слишком короткий блок')
    require(duration <= flex.get('max_minutes', float('inf')), 'Слишком длинный блок')
    if flex.get('not_before'):
        require(start >= stamp(flex['not_before']), 'Раньше разрешённого окна')
    if flex.get('not_after'):
        require(end <= stamp(flex['not_after']), 'Позже разрешённого окна')
    deadline = block.get('deadline')
    if deadline and deadline['kind'] == 'confirmed' and deadline.get('at'):
        require(end <= stamp(deadline['at']), 'Блок заканчивается после подтверждённого дедлайна')
    for other in context.get('events', []):
        if other.get('id') == event.get('id') or other.get('status') == 'cancelled' or other.get('transparency') == 'transparent':
            continue
        if isinstance(other.get('start'), dict) and 'date' in other['start']:
            zone = ZoneInfo(context.get('timezone', 'Asia/Almaty'))
            a = datetime.fromisoformat(other['start']['date']).replace(tzinfo=zone)
            b = datetime.fromisoformat(other['end']['date']).replace(tzinfo=zone)
        else:
            a, b = stamp(other['start']), stamp(other['end'])
        require(not (start < b and a < end), 'Пересечение с ' + other.get('summary', other.get('id', '?')))
    for protected in context.get('protected', []):
        require(not (start < stamp(protected['end']) and stamp(protected['start']) < end), 'Нарушено ограничение: ' + protected.get('label', '?'))
    if block['kind'] == 'work':
        zone = ZoneInfo(context.get('timezone', 'Asia/Almaty'))
        a, b = start.astimezone(zone), end.astimezone(zone)
        require(a.date() == b.date() and a.hour >= 8 and (b.hour < 23 or b.hour == 23 and b.minute == 0 and b.second == 0), 'Работа попадает в ночное окно 23–08')


def prepare(request):
    before = request['event']
    require(bool(before.get('id')), 'Нужен ID существующего события')
    # Self-only attendee entries are emitted by some Calendar connectors.
    # Keep them unchanged; reject guests and unknown attendee structures.
    attendees = before.get('attendees') or []
    require(isinstance(attendees, list), 'Некорректное поле attendees')
    require(len(attendees) <= 1 or not attendees, 'События с участниками требуют отдельного маршрута; автоматическая запись заблокирована')
    require(all(isinstance(a, dict) and a.get('is_self') is True for a in attendees),
            'События с участниками требуют отдельного маршрута; автоматическая запись заблокирована')
    old, bounds = extract(before.get('description'), request.get('previous_block'))
    if old:
        require(before.get('summary') == old['area'].strip() + ' — ' + old['task'].strip(), 'Название изменено вручную; сначала согласуй его со структурированными данными')
    block = validate(merge(old or {}, request['block']))
    if old and old['flexibility']['movable'] is False:
        require(block['flexibility']['movable'] is False, 'Нельзя снять фиксацию обычным обновлением')
    after = copy.deepcopy(before)
    after['summary'] = block['area'].strip() + ' — ' + block['task'].strip()
    description = before.get('description') or ''
    after['description'] = (description[:bounds[0]] + section(block) + description[bounds[1]:]) if bounds else (description + ('\n\n' if description else '') + section(block))
    if 'time' in request:
        require(block['flexibility']['movable'], 'Фиксированное событие нельзя переносить')
        require(set(request['time']) == {'start', 'end'}, 'Нужны обе временные границы')
        after.update(request['time'])
    check_schedule(after, block, request['context'])
    changes = {key: {'before': before.get(key), 'after': after[key]} for key in ('summary', 'description', 'start', 'end') if before.get(key) != after.get(key)}
    payload = {'event_id': before['id'], 'update_scope': 'this_instance'}
    mapping = {'summary': 'title', 'description': 'description', 'start': 'start_time', 'end': 'end_time'}
    for key in changes:
        payload[mapping[key]] = stamp(after[key]).isoformat() if key in ('start', 'end') else after[key]
    return {'schema_version': VERSION, 'before': before, 'after': after, 'before_hash': digest(before), 'diff': changes, 'connector_payload': payload, 'block': block}



# Independent execution status: does not touch the existing Guard-managed block.
EXEC_OPEN = "[AMIR-EXECUTION-STATUS:v1]"
EXEC_CLOSE = "[/AMIR-EXECUTION-STATUS]"
EXEC_STATES = {"PLANNED", "DONE", "PARTIAL", "CANCELLED_PLAN", "UNKNOWN"}
EXEC_KEYS = {"schema_version", "status", "planned_start", "planned_end",
             "actual_start", "actual_end", "evidence"}


def validate_execution(record):
    require(isinstance(record, dict) and set(record) == EXEC_KEYS,
            "Execution status requires exact v1 fields")
    require(record["schema_version"] == 1, "Unknown execution status schema")
    require(record["status"] in EXEC_STATES, "Unknown execution status")
    planned_a, planned_b = stamp(record["planned_start"]), stamp(record["planned_end"])
    require(planned_a < planned_b, "Invalid original planned interval")
    actual_a, actual_b = record["actual_start"], record["actual_end"]
    if actual_a is not None:
        actual_a = stamp(actual_a)
    if actual_b is not None:
        actual_b = stamp(actual_b)
    require(actual_a is None or actual_b is None or actual_a < actual_b,
            "Invalid actual interval")
    if record["status"] in {"PLANNED", "UNKNOWN", "CANCELLED_PLAN"}:
        require(actual_a is None and actual_b is None,
                "Non-completion status cannot include actual duration")
    evidence = record["evidence"]
    require(evidence is None or (isinstance(evidence, str) and 0 < len(evidence) <= 500),
            "Invalid evidence field")
    if record["status"] in {"DONE", "PARTIAL", "CANCELLED_PLAN"}:
        require(bool(evidence), "Confirmed status needs a factual source")
    return record


def read_execution(description):
    description = description or ""
    if EXEC_OPEN not in description and EXEC_CLOSE not in description:
        return None, None
    require(description.count(EXEC_OPEN) == 1 and description.count(EXEC_CLOSE) == 1,
            "Corrupt or duplicated execution status markers")
    a = description.index(EXEC_OPEN)
    b = description.index(EXEC_CLOSE)
    require(b > a, "Inverted execution status markers")
    body = description[a + len(EXEC_OPEN):b].strip()
    try:
        record = json.loads(body)
    except (ValueError, TypeError) as exc:
        # Calendar can wrap a long plain-text line at word boundaries even inside
        # a JSON string. Recover only those inserted physical line breaks.
        repaired = []
        in_string = False
        escaped = False
        for char in body:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                if char in ("\n", "\r") and in_string:
                    repaired.append(" ")
                    continue
            elif char == '"':
                in_string = True
            repaired.append(char)
        try:
            record = json.loads("".join(repaired))
        except (ValueError, TypeError) as second:
            raise GuardError("Malformed execution status JSON") from second
    validate_execution(record)
    return record, (a, b + len(EXEC_CLOSE))


def render_execution(record):
    # Avoid whitespace inside quoted values: Calendar word-wrap would corrupt JSON.
    compact = canonical(validate_execution(record)).replace(" ", "\\u0020")
    return EXEC_OPEN + "\n" + compact + "\n" + EXEC_CLOSE


def prepare_execution(request):
    """Metadata-only update: preserves times, attendees and all existing Guard text."""
    require(isinstance(request, dict) and
            set(request) == {"operation", "event", "execution", "context"},
            "Execution operation requires operation, event, execution, context")
    before = request["event"]
    context = request["context"]
    require(isinstance(before, dict) and bool(before.get("id")),
            "Existing event ID required")
    require(before.get("status") != "cancelled",
            "Cancelled events belong in Changes; do not revive them")
    require(isinstance(context, dict) and context.get("complete") is True,
            "Calendar context must be complete")
    start, end = stamp(before.get("start")), stamp(before.get("end"))
    require(start < end and stamp(context["window_start"]) <= start and
            end <= stamp(context["window_end"]), "Event outside verified window")
    attendees = before.get("attendees") or []
    require(isinstance(attendees, list) and len(attendees) <= 1 and
            all(isinstance(a, dict) and a.get("is_self") is True for a in attendees),
            "Events with guests require separate handling")
    new_record = validate_execution(request["execution"])
    desc = before.get("description") or ""
    old_record, bounds = read_execution(desc)
    if old_record is not None:
        for field in ("planned_start", "planned_end"):
            require(stamp(new_record[field]) == stamp(old_record[field]),
                    "Original plan is immutable; preserve planned_start/end")
    rendered = render_execution(new_record)
    after = copy.deepcopy(before)
    if bounds is None:
        after["description"] = desc + ("\n\n" if desc else "") + rendered
    else:
        after["description"] = desc[:bounds[0]] + rendered + desc[bounds[1]:]
    changes = ({"description": {"before": before.get("description"),
                                "after": after["description"]}}
               if (before.get("description") or "") != after["description"] else {})
    payload = {"event_id": before["id"], "update_scope": "this_instance"}
    if changes:
        payload["description"] = after["description"]
    return {"schema_version": VERSION, "operation": "execution_status",
            "before": before, "after": after, "before_hash": digest(before),
            "diff": changes, "connector_payload": payload,
            "execution": new_record}



def check_current(plan, current):
    require(digest(current) == plan['before_hash'], 'Событие изменилось после подготовки; перечитай и подготовь заново')
    return {'ok': True, 'connector_payload': plan['connector_payload']}


def verify(plan, actual):
    require(actual.get('id') == plan['before']['id'], 'Неверное событие')
    for key in ('summary', 'description', 'start', 'end'):
        expected, value = plan['after'].get(key), actual.get(key)
        equal = stamp(expected) == stamp(value) if key in ('start', 'end') else plain(expected) == plain(value) if key == 'description' else expected == value
        require(equal, 'Не совпадает после записи: ' + key)
    for key in ('location', 'attendees', 'recurrence', 'reminders', 'color_id', 'transparency'):
        require(plan['before'].get(key) == actual.get(key), 'Изменилось постороннее поле: ' + key)
    return {'ok': True}


def rollback(plan, current):
    verify(plan, current)
    payload = {'event_id': current['id'], 'update_scope': 'this_instance'}
    for key, value in plan['diff'].items():
        field = {'summary': 'title', 'description': 'description', 'start': 'start_time', 'end': 'end_time'}[key]
        payload[field] = stamp(value['before']).isoformat() if key in ('start', 'end') else value['before'] or ''
    return {'connector_payload': payload, 'warning': 'Перед откатом времени перечитать календарь и проверить допустимость прежнего интервала'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'check-current', 'verify', 'rollback', 'validate'))
    parser.add_argument('input', help='JSON file or - for stdin')
    args = parser.parse_args()
    try:
        data = json.load(sys.stdin if args.input == '-' else open(args.input, encoding='utf-8'))
        funcs = {'prepare': lambda: prepare(data), 'check-current': lambda: check_current(data['plan'], data['current']), 'verify': lambda: verify(data['plan'], data['current']), 'rollback': lambda: rollback(data['plan'], data['current']), 'validate': lambda: validate(data)}
        print(json.dumps(funcs[args.command](), ensure_ascii=False, indent=2))
    except (GuardError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        sys.exit(2)


if __name__ == '__main__':
    main()
