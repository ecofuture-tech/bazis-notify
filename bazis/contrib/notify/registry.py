# Copyright 2026 EcoFuture Technology Services LLC and contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
The notification types. An app declares its types in `<app>/notifications.py` (imported
when the apps are ready) with `register(...)`; `notify` refuses a type that is not
registered, and the category, the severity and the default action of a notification come
from its type, not from conditions in the model.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ImproperlyConfigured


#: the severities a client can render: an information, a success, a warning, an error
SEVERITIES = ('info', 'success', 'warning', 'error')

SLUG_RE = re.compile(r'[a-z0-9_]+(\.[a-z0-9_]+)*')


class UnknownNotificationTypeError(LookupError):
    """
    `notify` was called with a type that no app registered.
    """


@dataclass(frozen=True)
class NotificationType:
    #: the type of the notifications (`billing.purchase_declined`), stored in them
    slug: str
    #: the name of the type (a lazy string), for the admin and the documentation
    title: Any = None
    #: a group of types for the client (`billing`), by default the first part of the slug
    category: str = ''
    severity: str = 'info'
    #: the action of the notifications that are sent without one (`{"route": "/billing"}`)
    action: dict = field(default_factory=dict)


_types: dict[str, NotificationType] = {}


def register(
    slug: str,
    *,
    title: Any = None,
    category: str | None = None,
    severity: str = 'info',
    action: dict | None = None,
) -> NotificationType:
    """
    Registers a notification type; registering the same declaration again does nothing,
    another declaration of a registered slug is an error.
    """
    if not SLUG_RE.fullmatch(slug):
        raise ImproperlyConfigured(
            f'Notification type {slug!r}: a slug of lowercase words joined by dots expected'
        )
    if severity not in SEVERITIES:
        raise ImproperlyConfigured(
            f'Notification type {slug!r}: severity {severity!r} is not one of {SEVERITIES}'
        )
    notification_type = NotificationType(
        slug=slug,
        title=title,
        category=category or slug.split('.')[0],
        severity=severity,
        action=dict(action or {}),
    )
    registered = _types.setdefault(slug, notification_type)
    if registered != notification_type:
        raise ImproperlyConfigured(f'Notification type {slug!r} is registered differently')
    return registered


def get_type(slug: str) -> NotificationType:
    """
    The registered type of the slug; raises UnknownNotificationTypeError.
    """
    try:
        return _types[slug]
    except KeyError:
        raise UnknownNotificationTypeError(
            f'Notification type {slug!r} is not registered: register it in '
            f'<app>/notifications.py with bazis.contrib.notify.registry.register'
        ) from None


def registered_types() -> list[NotificationType]:
    return list(_types.values())
