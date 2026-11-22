"""Business-time arithmetic for optional SLA working schedules."""
from datetime import date, datetime, time, timedelta


def _interval(day, schedule, timezone):
    if day.weekday() not in schedule["days"] or day.isoformat() in schedule["holidays"]:
        return None
    start = datetime.combine(day,time.fromisoformat(schedule["start"]),tzinfo=timezone)
    end = datetime.combine(day,time.fromisoformat(schedule["end"]),tzinfo=timezone)
    return start,end


def add_minutes(start, minutes, schedule=None):
    if not schedule:
        return start + timedelta(minutes=minutes)
    remaining = timedelta(minutes=minutes)
    current = start
    while True:
        interval = _interval(current.date(),schedule,current.tzinfo)
        if interval:
            opening,closing = interval
            cursor = max(current,opening)
            available = closing - cursor
            if available > timedelta() and remaining <= available:
                return cursor + remaining
            remaining -= max(timedelta(),available)
        current = datetime.combine(current.date() + timedelta(days=1),time.min,tzinfo=current.tzinfo)


def elapsed_seconds(begin, end, schedule=None):
    if not schedule:
        return (end - begin).total_seconds()
    if end < begin:
        return -elapsed_seconds(end,begin,schedule)
    day,total = begin.date(),0.0
    while day <= end.date():
        interval = _interval(day,schedule,begin.tzinfo)
        if interval:
            opening,closing = interval
            total += max(0,(min(end,closing) - max(begin,opening)).total_seconds())
        if day == end.date():
            break
        day += timedelta(days=1)
    return total


def normalize_schedule(value):
    if value is None:
        return None
    if not isinstance(value,dict):
        raise ValueError("invalid SLA calendar")
    days,start,end,holidays = (value.get(key) for key in ("days","start","end","holidays"))
    if (not isinstance(days,list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days)
            or not isinstance(start,str) or not isinstance(end,str) or not isinstance(holidays,list) or len(holidays)>365):
        raise ValueError("enter working days, hours, and up to 365 holidays")
    begin,finish = time.fromisoformat(start),time.fromisoformat(end)
    if begin >= finish or begin.tzinfo or finish.tzinfo or begin.second or finish.second or begin.microsecond or finish.microsecond:
        raise ValueError("enter opening and closing hours in HH:MM server time")
    return dict(days=sorted(set(days)),start=begin.isoformat(timespec="minutes"),end=finish.isoformat(timespec="minutes"),
                holidays=sorted({date.fromisoformat(day).isoformat() for day in holidays}))
