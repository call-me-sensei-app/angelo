# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small native text presentation helpers; URLs never execute markup or scripts."""

import re
from urllib.parse import urlsplit


def link_text(value):
    links, parts, length, previous = [], [], 0, 0
    for match in re.finditer(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", value):
        prefix = value[previous:match.start()]
        parts.append(prefix)
        length += len(prefix)
        label, url = match.groups()
        parts.append(label)
        links.append((length, length + len(label), url))
        length += len(label)
        previous = match.end()
    parts.append(value[previous:])
    return "".join(parts), links


def safe_url(value):
    url = urlsplit(value)
    return url.scheme in {"http", "https"} and bool(url.hostname) and not url.username
