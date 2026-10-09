"""Read-only, fail-closed preview of candidate Calendar slots (v0.11)."""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Almaty")

class PreviewError(ValueError):
    pass

def require(ok, message):
    if not ok:
        raise PreviewError(message)

def stamp(value):
    if isinstance(value, dict):
        require("dateTime" in value, "All-day event requires explicit handling")
        value = value["dateTime"]
    require(isinstance(value, str), "Offset-aware ISO datetime required")
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreviewError("Invalid ISO datetime") from exc
    require(d.tzinfo is not None, "Timezone offset required")
    return d.astimezone(TZ)

def hhmm(s):
    require(isinstance(s, str) and len(s) == 5 and s[2] == ":", "Expected HH:MM")
    try:
        h, m = map(int, s.split(":"))
        return time(h, m)
    except ValueError as exc:
        raise PreviewError("Invalid HH:MM") from exc

def overlap(a, b, c, d):
    return a < d and c < b

def preview_slots(request, *, now=None):
    """Caller supplies full fresh events, deadlines and protected windows.

    Nothing is stored or written. Suggestions require a separate Guard write.
    """
    require(isinstance(request, dict), "Request must be object")
    require(request.get("constraints_attested") is True,
            "Review sleep, meals, prayers, commute and other hidden constraints")
    cal, task, rules, protected = (request.get(x) for x in
                                    ("calendar", "task", "rules", "protected"))
    require(all((isinstance(cal, dict), isinstance(task, dict),
                 isinstance(rules, dict), isinstance(protected, list))),
            "calendar/task/rules/protected required")
    require(cal.get("complete") is True and cal.get("next_page_token") in (None, ""),
            "All calendar pages required")
    now = now or datetime.now(TZ)
    observed = stamp(cal.get("observed_at"))
    age = (now.astimezone(TZ)-observed).total_seconds()/60
    require(-5 <= age <= 30, "Calendar stale or future dated")
    left, right = stamp(cal.get("window_start")), stamp(cal.get("window_end"))
    require(left < right and right-left <= timedelta(days=14),
            "Invalid calendar window; max 14 days")
    raw_events = cal.get("events")
    require(isinstance(raw_events, list) and 0 < len(raw_events) <= 300,
            "Calendar events missing or excessive")
    ids = [e.get("id") for e in raw_events]
    require(all(isinstance(i,str) and bool(i) for i in ids)
            and len(set(ids)) == len(ids), "Duplicate/missing event IDs")
    require(isinstance(task.get("id"),str) and bool(task["id"]), "Task ID required")
    require(task.get("status") not in ("Completed","completed","Done"),
            "Completed task cannot be scheduled")
    duration = task.get("duration_minutes")
    require(type(duration) is int and 15 <= duration <= 360 and duration % 15 == 0,
            "Duration: 15..360 minutes in 15-minute increments")
    earliest, deadline = stamp(task.get("earliest_start")),stamp(task.get("finish_before"))
    require(left <= earliest < deadline <= right, "Task bounds outside calendar window")
    require(task.get("deadline_kind") in ("confirmed","internal"),
            "Deadline must be classified as confirmed or internal")
    if task["deadline_kind"] == "confirmed":
        require(bool(task.get("deadline_source")), "Confirmed deadline requires source")
    linked = task.get("linked_event_id")
    if linked:
        require(ids.count(linked)==1 and task.get("linked_event_movable") is True,
                "Linked event missing or not explicitly movable")
    fs, fe = hhmm(rules.get("focus_start")), hhmm(rules.get("focus_end"))
    require(fs < fe, "Invalid focus window")
    gap, travel_gap = rules.get("transition_minutes"),rules.get("pre_travel_minutes")
    require(type(gap) is int and 0 <= gap <= 90, "Invalid transition minutes")
    require(type(travel_gap) is int and 0 <= travel_gap <= 120,
            "Invalid preparation-for-travel minutes")
    events = []
    soft_events = []
    for e in raw_events:
        a, b = stamp(e.get("start")),stamp(e.get("end"))
        require(a < b, "Invalid Calendar event interval")
        if e["id"] == linked or e.get("status") == "cancelled":
            continue
        label = e.get("summary", "")
        transparent = e.get("transparency") == "transparent"
        if label.startswith("[AI][HARD]"):
            require(not transparent, "AI HARD event must be busy/opaque")
        if label.startswith("[AI][SOFT]"):
            require(transparent, "AI SOFT event must be free/transparent")
        if transparent:
            if label.startswith("[AI][SOFT]"):
                soft_events.append((a,b))
            continue
        travel = e.get("kind") == "travel" or "Дорога" in label
        events.append((a,b,travel))
    blocks = []
    for p in protected:
        a,b = stamp(p.get("start")),stamp(p.get("end"))
        require(a < b, "Invalid protected interval")
        blocks.append((a,b))
    initial = max(earliest,now.astimezone(TZ))
    step=timedelta(minutes=15)
    current=initial.replace(minute=(initial.minute//15)*15,second=0,microsecond=0)
    if current < initial: current += step
    candidates=[]
    reasons={"busy_or_protected":0,"focus_or_deadline":0,"transition_or_travel":0}
    for _ in range(14*24*4+1):
        end=current+timedelta(minutes=duration)
        if end > deadline: break
        if not (current.date()==end.date() and fs <= current.timetz().replace(tzinfo=None)
                and end.timetz().replace(tzinfo=None) <= fe):
            reasons["focus_or_deadline"]+=1
        elif any(overlap(current,end,a,b) for a,b in blocks) or any(
                overlap(current,end,a,b) for a,b,_ in events):
            reasons["busy_or_protected"]+=1
        elif any((timedelta(0) <= a-end < timedelta(minutes=max(gap,travel_gap if t else 0)))
                 or (timedelta(0) <= current-b < timedelta(minutes=gap))
                 for a,b,t in events):
            reasons["transition_or_travel"]+=1
        else:
            soft_minutes = sum(
                max(0, int((min(end,b)-max(current,a)).total_seconds() // 60))
                for a,b in soft_events if overlap(current,end,a,b))
            # Soft time stays feasible but loses priority to an equally practical free slot.
            # Each 15 minutes of overlap costs roughly two hours of time-of-day preference.
            soft_penalty = 8 * ((soft_minutes + 14) // 15)
            score=(current.date()-initial.date()).days*100 + abs(
                current.hour*60+current.minute-13*60)//15 + soft_penalty
            candidates.append((score,current,end,soft_minutes))
        current+=step
    candidates.sort(key=lambda x:(x[0],x[1]))
    return {"status":"FEASIBLE" if candidates else "NO_FEASIBLE_SLOT",
            "mode":"READ_ONLY","calendar_writes":0,"planner_writes":0,
            "task_id":task["id"],"calendar_event_count":len(raw_events),
            "deadline_kind":task["deadline_kind"],
            "suggestions":[{"start":a.isoformat(timespec="minutes"),
                            "end":b.isoformat(timespec="minutes"),"score":score,
                            "soft_overlap_minutes":soft_minutes}
                           for score,a,b,soft_minutes in candidates[:3]],
            "feasible_count":len(candidates),"rejections":reasons,
            "limitations":["Supply complete live events and all hidden constraints",
                           "A Calendar change requires the separate Guard verify workflow"]}
