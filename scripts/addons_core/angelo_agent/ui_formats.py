# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dates, times, numbers and sizes as Angelo's interface shows them, in every language.

Blender has no locale date or number API, so each format is one a translator can make right and that reads the same
everywhere: a date is a whole template per month (``"{day} November {year}"``: a translation reorders it, inflects the
month or writes ``{year}年11月{day}日``; never 11/05 against 05/11), a time is the 24-hour clock, a fraction uses the
language's decimal mark (a translatable "." with the context "Decimal separator": "8,4" in German and French) and a
size is a number in a translatable unit template (``"{value} MB"``). Drawing code formats through here, never with
``strftime`` or a ``"%.1f MB"`` of its own (``test_native_ui_lints``). Text a model reads keeps its own fixed formats
and is never translated.

    format_date(epoch) -> str          "5 November 2026" in the interface language; "" when the platform cannot say
    format_time(epoch) -> str          "18:20"
    format_date_time(epoch) -> str     "5 November 2026, 18:20"
    format_clock(epoch, now=None)      the time alone today, else the date and time
    format_decimal(value, places=1)    "8.4" with the language's decimal mark
    format_bytes(count) -> str         "940 bytes", "8.4 KB", "212 MB", "1.8 GB": one decimal below ten
    format_pixels(size) -> str         a texture size: "1K" for 1024, "1.5K" for 1536, "512" below
"""

import time

from .ui_text import iface_, n_, plural_

MONTHS = (n_("{day} January {year}"), n_("{day} February {year}"), n_("{day} March {year}"),
          n_("{day} April {year}"), n_("{day} May {year}"), n_("{day} June {year}"), n_("{day} July {year}"),
          n_("{day} August {year}"), n_("{day} September {year}"), n_("{day} October {year}"),
          n_("{day} November {year}"), n_("{day} December {year}"))
DATE_TIME = n_("{date}, {time}")
PIXELS = n_("{count}K")   # A texture's size in thousands of pixels, as artists write it: 1K, 2K, 4K.
UNITS = ((1 << 30, n_("{value} GB")), (1 << 20, n_("{value} MB")), (1 << 10, n_("{value} KB")))


def _local(epoch):
    try:
        return time.localtime(epoch)
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def format_date(epoch):
    moment = _local(epoch)
    if moment is None:
        return ""
    return iface_(MONTHS[moment.tm_mon - 1], day=str(moment.tm_mday), year=str(moment.tm_year))


def format_time(epoch):
    moment = _local(epoch)
    return "" if moment is None else "%02d:%02d" % (moment.tm_hour, moment.tm_min)


def format_date_time(epoch):
    date, clock = format_date(epoch), format_time(epoch)
    return iface_(DATE_TIME, date=date, time=clock) if date else ""


def format_clock(epoch, now=None):
    """The time alone when ``epoch`` is today, else the date and the time; "" when the platform cannot convert it."""
    moment, today = _local(epoch), _local(time.time() if now is None else now)
    if moment is None or today is None:
        return ""
    return format_time(epoch) if moment[:3] == today[:3] else format_date_time(epoch)


def format_decimal(value, places=1):
    """``value`` rounded to ``places`` decimals, written with the interface language's decimal mark."""
    text = "%.*f" % (places, value)
    return text.replace(".", iface_(".", "Decimal separator")) if places > 0 else text   # "," in German, French...


def format_bytes(count):
    count = max(0, int(count))
    for limit, template in UNITS:
        if count >= limit:
            value = count / limit
            return iface_(template, value=format_decimal(value) if value < 9.95 else str(round(value)))
    return plural_(count, "{count} byte", "{count} bytes")


def format_pixels(size):
    if size < 1024:
        return str(size)
    count = str(size // 1024) if size % 1024 == 0 else format_decimal(size / 1024)
    return iface_(PIXELS, count=count)
