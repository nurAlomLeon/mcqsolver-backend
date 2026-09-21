"""Shared local timezone for automation scheduling.

The server clock runs in UTC (see ``TIME_ZONE`` in settings), but every
automated notification should respect when the learner is actually awake.
The audience is Bangladesh-based, so all "daytime window" gating is computed
against Asia/Dhaka regardless of where the server itself is hosted.
"""
from zoneinfo import ZoneInfo

DHAKA_TZ = ZoneInfo('Asia/Dhaka')
