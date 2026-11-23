"""A stand-in for the orbit-calendar helper: FROM TO → two events a day (JSON), or an error ($FAKE_CAL=denied)."""
import json
import os
import sys
from datetime import datetime, timedelta

if os.environ.get("FAKE_CAL") == "denied":
    print(json.dumps({"error": "Orbit may not read your calendars.", "denied": True}))
    sys.exit(0)
frm, to = float(sys.argv[1]), float(sys.argv[2])
day = datetime.fromtimestamp(frm).replace(hour=0, minute=0, second=0, microsecond=0)
events = []
while day.timestamp() < to:
    for h, title, extra in [(9, "Standup", {"url": "https://meet.google.com/abc-defg-hij", "attendees": 4,
                                            "people": ["Ann", "Bo", "Cy"], "organizer": "Ann", "mine": False,
                                            "guests": [{"name": "Ann", "status": "accepted", "me": False},
                                                       {"name": "Bo", "status": "accepted", "me": False},
                                                       {"name": "Cy", "status": "declined", "me": False},
                                                       {"name": "Me", "status": "accepted", "me": True}],
                                            "notes": "Agenda: the deploy, https://example.com/plan", "recurring": True}),
                            (9.25, "Release sync", {"attendees": 2, "people": ["Dee"], "organizer": "Dee", "mine": False,
                                                    "guests": [{"name": "Dee", "status": "accepted", "me": False}]}),  # overlaps Standup
                            (12, "Focus block", {"mine": True}),  # your own block: noise
                            (14, "Design review", {"location": "Room 4", "notes": "Join: https://acme.zoom.us/j/123456?pwd=x.",
                                                   "attendees": 2, "people": ["Dee"], "organizer": "Dee", "mine": False})]:
        start = (day + timedelta(hours=h)).timestamp()
        if frm <= start < to:
            events.append({"id": f"{title}-{day:%m%d}", "title": title, "start": start, "end": start + 1800,
                           "all_day": False, "calendar": "Work", "location": "", "url": "", "notes": "", **extra})
    day += timedelta(days=1)
print(json.dumps({"events": events}))
