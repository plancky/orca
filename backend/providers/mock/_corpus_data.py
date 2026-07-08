"""Deterministic mock corpus (data table only — no DB logic).

``build_corpus(now)`` returns emails / events / files sized so **every** sample +
edge-case query in ``docs/PLAN.md`` (l.686-751) resolves against the seeded data:

* Turkish Airlines booking email (with PNR) ...... cancel-flight
* Acme Corp meeting event + emails + Drive doc ... prepare-for-meeting
* ``sarah@company.com`` budget emails ............ single-service gmail
* recent PDFs in Drive ........................... "PDFs from last month"
* out-of-office Drive doc + next-week events ..... conflict detection
* a "proposal" email thread ...................... context follow-up
* TWO distinct "John" contacts + meetings ........ ambiguity → clarification
* calendar events next week ...................... single-service gcal

Dates are relative to ``now`` so temporal queries ("tomorrow", "next week",
"last month") resolve whenever the corpus is seeded. Business ids
(``email_id``/``event_id``/``file_id``) are stable so re-seeding upserts.
"""

# allow: SIZE_OK — one cohesive corpus fixture (a pure data table of emails /
# events / files). Splitting the three services into separate files fragments a
# single logical fixture and adds import ceremony for zero readability gain.

from datetime import datetime, timedelta
from typing import Any

_ME = "me@example.com"
DAY = timedelta(days=1)


def build_corpus(now: datetime) -> dict[str, list[dict[str, Any]]]:
    next_week = now + 7 * DAY
    ooo_end = next_week + 4 * DAY
    last_month = now - 22 * DAY
    return {
        "gmail": _emails(now, next_week),
        "gcal": _events(now, next_week),
        "gdrive": _files(now, next_week, ooo_end, last_month),
    }


def _emails(now: datetime, next_week: datetime) -> list[dict[str, Any]]:
    return [
        {
            "email_id": "tk-booking-001",
            "thread_id": "thr-turkish-airlines",
            "sender_email_id": "reservations@turkishairlines.com",
            "receiver_email_id": _ME,
            "subject": "Turkish Airlines Booking Confirmation — PNR TK4471",
            "content": (
                "Dear passenger, your Turkish Airlines flight is confirmed. "
                "Booking reference (PNR): TK4471. Flight TK0001 from Istanbul "
                f"(IST) to New York (JFK) departs {next_week:%Y-%m-%d} at 09:15. "
                "To cancel or change your reservation, reply to this email or "
                "visit Manage Booking."
            ),
            "labels": ["Travel", "Inbox"],
            "sent_at": now - 10 * DAY,
            "received_at": now - 10 * DAY,
        },
        {
            "email_id": "sarah-budget-001",
            "thread_id": "thr-q3-budget",
            "sender_email_id": "sarah@company.com",
            "receiver_email_id": _ME,
            "subject": "Q3 Budget Review",
            "content": (
                "Hi, attached is the Q3 budget breakdown. We are 8% under on "
                "marketing spend and need to reallocate the remaining budget "
                "before end of quarter. Let me know your thoughts. — Sarah"
            ),
            "labels": ["Work", "Finance"],
            "sent_at": now - 5 * DAY,
            "received_at": now - 5 * DAY,
        },
        {
            "email_id": "sarah-budget-002",
            "thread_id": "thr-q3-budget",
            "sender_email_id": "sarah@company.com",
            "receiver_email_id": _ME,
            "subject": "Re: Q3 Budget Review — updated figures",
            "content": (
                "Updated the budget spreadsheet with the finance team's latest "
                "numbers. The total budget is now 1.2M with a 50k contingency. "
                "Please approve so we can close the budget. — Sarah"
            ),
            "labels": ["Work", "Finance"],
            "sent_at": now - 4 * DAY,
            "received_at": now - 4 * DAY,
        },
        {
            "email_id": "acme-agenda-001",
            "thread_id": "thr-acme-corp",
            "sender_email_id": "john.doe@acmecorp.com",
            "receiver_email_id": _ME,
            "subject": "Agenda for our Acme Corp sync",
            "content": (
                "Looking forward to the meeting with Acme Corp. Agenda: 1) "
                "partnership scope, 2) pricing, 3) timeline. I've shared the "
                "Acme Corp partnership overview doc for your review."
            ),
            "labels": ["Work"],
            "sent_at": now - 2 * DAY,
            "received_at": now - 2 * DAY,
        },
        {
            "email_id": "proposal-001",
            "thread_id": "thr-proposal",
            "sender_email_id": "mike@partner.com",
            "receiver_email_id": _ME,
            "subject": "Project proposal draft",
            "content": (
                "Here is the first draft of the project proposal for the new "
                "engagement. The proposal covers scope, deliverables, and a "
                "rough estimate. Happy to walk through it."
            ),
            "labels": ["Work"],
            "sent_at": now - 3 * DAY,
            "received_at": now - 3 * DAY,
        },
        {
            "email_id": "proposal-002",
            "thread_id": "thr-proposal",
            "sender_email_id": "mike@partner.com",
            "receiver_email_id": _ME,
            "subject": "Re: Project proposal draft",
            "content": (
                "Revised the proposal per your feedback — tightened the scope "
                "and updated the estimate. This proposal is ready for sign-off."
            ),
            "labels": ["Work"],
            "sent_at": now - 1 * DAY,
            "received_at": now - 1 * DAY,
        },
        {
            "email_id": "john-smith-001",
            "thread_id": "thr-john-smith",
            "sender_email_id": "john.smith@example.com",
            "receiver_email_id": _ME,
            "subject": "Lunch next week?",
            "content": (
                "Hey, it's John Smith. Want to grab lunch next week and catch "
                "up? Let me know what day works for the meeting."
            ),
            "labels": ["Personal"],
            "sent_at": now - 6 * DAY,
            "received_at": now - 6 * DAY,
        },
        {
            "email_id": "john-doe-001",
            "thread_id": "thr-acme-corp",
            "sender_email_id": "john.doe@acmecorp.com",
            "receiver_email_id": _ME,
            "subject": "Re: project timeline",
            "content": (
                "This is John Doe from Acme Corp. Can we move the meeting to "
                "later in the week to finalize the project timeline?"
            ),
            "labels": ["Work"],
            "sent_at": now - 2 * DAY,
            "received_at": now - 2 * DAY,
        },
    ]


def _events(now: datetime, next_week: datetime) -> list[dict[str, Any]]:
    tomorrow = now + DAY
    return [
        {
            "event_id": "acme-meeting-001",
            "title": "Meeting with Acme Corp",
            "description": "Quarterly partnership sync with the Acme Corp team.",
            "location": "Zoom",
            "start_at": tomorrow.replace(hour=10, minute=0),
            "end_at": tomorrow.replace(hour=11, minute=0),
            "attendees": ["john.doe@acmecorp.com", _ME],
        },
        {
            "event_id": "nextweek-standup",
            "title": "Team Standup",
            "description": "Weekly engineering standup.",
            "location": "Meet",
            "start_at": next_week.replace(hour=9, minute=0),
            "end_at": next_week.replace(hour=9, minute=30),
            "attendees": [_ME],
        },
        {
            "event_id": "nextweek-review",
            "title": "Product Review",
            "description": "Review the product roadmap for the quarter.",
            "location": "Room 4B",
            "start_at": (next_week + DAY).replace(hour=14, minute=0),
            "end_at": (next_week + DAY).replace(hour=15, minute=0),
            "attendees": [_ME, "sarah@company.com"],
        },
        {
            "event_id": "nextweek-client-call",
            "title": "Client Call",
            "description": "Call with the client during the OOO window.",
            "location": "Phone",
            "start_at": (next_week + DAY).replace(hour=11, minute=0),
            "end_at": (next_week + DAY).replace(hour=12, minute=0),
            "attendees": [_ME],
        },
        {
            "event_id": "john-smith-1on1",
            "title": "1:1 with John Smith",
            "description": "Catch-up lunch with John Smith.",
            "location": "Cafe",
            "start_at": (now + 3 * DAY).replace(hour=12, minute=0),
            "end_at": (now + 3 * DAY).replace(hour=13, minute=0),
            "attendees": ["john.smith@example.com", _ME],
        },
        {
            "event_id": "john-doe-sync",
            "title": "Sync with John Doe",
            "description": "Project timeline sync with John Doe from Acme Corp.",
            "location": "Zoom",
            "start_at": (now + 4 * DAY).replace(hour=15, minute=0),
            "end_at": (now + 4 * DAY).replace(hour=16, minute=0),
            "attendees": ["john.doe@acmecorp.com", _ME],
        },
    ]


def _files(
    now: datetime, next_week: datetime, ooo_end: datetime, last_month: datetime
) -> list[dict[str, Any]]:
    return [
        {
            "file_id": "acme-doc-001",
            "name": "Acme Corp Partnership Overview.docx",
            "mime_type": "application/vnd.google-apps.document",
            "content": (
                "Acme Corp Partnership Overview\n\nBackground: Acme Corp is a "
                "strategic partner. Scope: joint go-to-market. Pricing: tiered. "
                "Next steps: finalize the agreement at the upcoming meeting."
            ),
            "owner": _ME,
            "modified_at": now - 2 * DAY,
        },
        {
            "file_id": "pdf-financials-001",
            "name": "Q3 Financial Report.pdf",
            "mime_type": "application/pdf",
            "content": (
                "Q3 Financial Report\n\nRevenue grew 12% QoQ. Operating margin "
                "held at 22%. The finance team recommends holding the budget "
                "flat into Q4."
            ),
            "owner": _ME,
            "modified_at": last_month,
        },
        {
            "file_id": "pdf-contract-001",
            "name": "Vendor Contract Draft.pdf",
            "mime_type": "application/pdf",
            "content": (
                "Vendor Contract Draft\n\nThis agreement sets out the terms "
                "between the parties, including payment schedule and termination "
                "clauses. Draft for legal review."
            ),
            "owner": _ME,
            "modified_at": last_month - DAY,
        },
        {
            "file_id": "ooo-doc-001",
            "name": "Out of Office Schedule.docx",
            "mime_type": "application/vnd.google-apps.document",
            "content": (
                "Out of Office Notice\n\nI will be out of office from "
                f"{next_week:%Y-%m-%d} through {ooo_end:%Y-%m-%d}. Please avoid "
                "scheduling any meetings or calls during this period. For "
                "urgent matters, contact the support team."
            ),
            "owner": _ME,
            "modified_at": now - DAY,
        },
    ]
