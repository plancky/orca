import calendar
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def resolve_timeframe(
    phrase: str, now: datetime, tz: ZoneInfo
) -> dict[str, str] | None:
    if not phrase:
        return None

    phrase = phrase.lower().strip()

    # Ensure 'now' is in the target tz
    now_tz = now.astimezone(tz)
    today = now_tz.date()

    start_dt = None
    end_dt = None

    if "tomorrow" in phrase:
        target_date = today + timedelta(days=1)
        start_dt = datetime.combine(target_date, time.min, tzinfo=tz)
        end_dt = datetime.combine(target_date, time.max, tzinfo=tz)

    elif "next week" in phrase:
        # Next week Monday to Sunday
        days_ahead = 7 - today.weekday()
        next_monday = today + timedelta(days=days_ahead)
        next_sunday = next_monday + timedelta(days=6)
        start_dt = datetime.combine(next_monday, time.min, tzinfo=tz)
        end_dt = datetime.combine(next_sunday, time.max, tzinfo=tz)

    elif "last month" in phrase:
        # 1st of last month to last day of last month
        first_day_of_this_month = today.replace(day=1)
        last_day_of_last_month = first_day_of_this_month - timedelta(days=1)
        first_day_of_last_month = last_day_of_last_month.replace(day=1)
        start_dt = datetime.combine(first_day_of_last_month, time.min, tzinfo=tz)
        end_dt = datetime.combine(last_day_of_last_month, time.max, tzinfo=tz)

    elif phrase.startswith("next "):
        # e.g., "next tuesday"
        day_names = [d.lower() for d in calendar.day_name]
        day_name = phrase.replace("next ", "").strip()
        if day_name in day_names:
            target_weekday = day_names.index(day_name)
            current_weekday = today.weekday()
            days_ahead = (target_weekday - current_weekday) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_date = today + timedelta(days=days_ahead)
            start_dt = datetime.combine(target_date, time.min, tzinfo=tz)
            end_dt = datetime.combine(target_date, time.max, tzinfo=tz)

    if start_dt and end_dt:
        return {"start": start_dt.isoformat(), "end": end_dt.isoformat()}
    return None
