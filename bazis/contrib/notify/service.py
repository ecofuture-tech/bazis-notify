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
Creating the notifications of the users and delivering them:

- stored: a row per recipient, which the user lists, reads and deletes through the API
  (`bazis.contrib.notify.router`);
- live: with bazis-ws, after the commit, `notification_created` to the channel of each
  recipient whose user model has `UserWsMixin`, and `notifications_changed` when the
  notifications of a user are read, unread or deleted;
- by e-mail with `email=True`: after the commit, one batch through the default mailer of
  the project (the core configures it from `BAZIS_EMAIL_*`).
"""

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from functools import partial
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage, mailers
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Count, QuerySet
from django.utils import timezone, translation
from django.utils.functional import Promise

from .models import Notification
from .registry import get_type


logger = logging.getLogger(__name__)

#: the keys of `metadata` (at any depth) that contain one of these are not stored: the
#: notifications are shown to the user and sent over the socket
PRIVATE_METADATA_MARKERS = (
    'token',
    'secret',
    'password',
    'receipt',
    'authorization',
    'api_key',
    'apikey',
    'credential',
    'cookie',
    'traceback',
)

#: a text of a notification: a string, a lazy string or a function of the recipient
Text = str | Promise | Callable[[Any], str]


def notify(
    users: Any,
    notification_type: str,
    title: Text,
    body: Text = '',
    *,
    params: Mapping[str, Any] | None = None,
    action: dict | None = None,
    metadata: dict | None = None,
    dedupe_key: str | None = None,
    email: bool = False,
) -> list[Notification]:
    """
    Creates a notification of the registered type for each user (a user or an iterable of
    users; None and repeated users are skipped) and delivers it after the commit (see the
    module). Returns the notifications created.

    `title` and `body` are rendered for each recipient in his language (`user.language`
    of `UserLanguageMixin`, else LANGUAGE_CODE): a lazy string is translated there and
    then formatted with `params` (`_('%(product)s was declined')`, `params={'product':
    ...}`), a function is called with the user. `action` defaults to the action of the
    type; the keys of `metadata` that look like secrets are dropped.

    With a `dedupe_key`, a user who already has a notification with that key gets no new
    one (nor its e-mail): a retried call does not notify twice.
    """
    notification_type = get_type(notification_type)
    rows = []
    for user in _recipients(users):
        with translation.override(getattr(user, 'language', None) or settings.LANGUAGE_CODE):
            rows.append(
                Notification(
                    user=user,
                    notification_type=notification_type.slug,
                    title=_render(title, params, user),
                    body=_render(body, params, user),
                    action=_jsonable(notification_type.action if action is None else action),
                    metadata=sanitize_metadata(_jsonable(metadata or {})),
                    dedupe_key=dedupe_key,
                )
            )
    if not rows:
        return []

    with transaction.atomic():
        Notification.objects.bulk_create(rows, ignore_conflicts=dedupe_key is not None)
        if dedupe_key is not None:
            # the rows skipped as duplicates are not in the database (the ids are generated
            # by the application, so each row knows its id before the insert)
            created = set(
                Notification.objects.filter(pk__in=[row.pk for row in rows]).values_list(
                    'pk', flat=True
                )
            )
            rows = [row for row in rows if row.pk in created]
        if rows:
            transaction.on_commit(partial(_publish_created, rows), robust=True)
            if email and (messages := [_email(row) for row in rows if getattr(row.user, 'email', '')]):
                transaction.on_commit(partial(_send_mail, messages), robust=True)
    return rows


def notify_all(
    users: Iterable,
    notification_type: str,
    title: Text,
    body: Text = '',
    *,
    batch_size: int = 500,
    **kwargs,
) -> int:
    """
    `notify` for many users (a queryset is read with `iterator()`), `batch_size` users per
    insert; returns the number of notifications created. Run it outside a transaction (a
    task of bazis-bg with `is_transaction = False`): each batch commits and is delivered on
    its own. With a `dedupe_key` a retried run skips the users already notified.
    """
    if isinstance(users, QuerySet):
        users = users.iterator(chunk_size=batch_size)
    created = 0
    batch = []
    for user in users:
        batch.append(user)
        if len(batch) == batch_size:
            created += len(notify(batch, notification_type, title, body, **kwargs))
            batch = []
    if batch:
        created += len(notify(batch, notification_type, title, body, **kwargs))
    return created


def unread_count(user) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def read_all(user) -> int:
    """
    Marks every unread notification of the user read; returns their number.
    """
    count = Notification.objects.filter(user=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    if count:
        notifications_changed(user)
    return count


def clear(user) -> int:
    """
    Deletes every notification of the user; returns their number.
    """
    count, _ = Notification.objects.filter(user=user).delete()
    if count:
        notifications_changed(user)
    return count


def delete_expired(days: int | None = None) -> int:
    """
    Deletes the notifications created more than `days` (BAZIS_NOTIFY_RETENTION_DAYS)
    days ago, read or not; nothing without a retention. Returns their number. Run it
    periodically (a cron task of bazis-bg).
    """
    days = days if days is not None else settings.BAZIS_NOTIFY_RETENTION_DAYS
    if not days:
        return 0
    count, _ = Notification.objects.filter(
        dt_created__lt=timezone.now() - timedelta(days=days)
    ).delete()
    return count


def notifications_changed(user) -> None:
    """
    After the commit, tells the sessions of the user that his notifications changed
    (`notifications_changed` with the unread count), with bazis-ws.
    """
    if _has_channel(user):
        transaction.on_commit(partial(_publish_changed, user), robust=True)


def serialize(notification: Notification) -> dict:
    """
    The notification as the attributes of its resource, for the socket.
    """
    return {
        'id': str(notification.pk),
        'notification_type': notification.notification_type,
        'title': notification.title,
        'body': notification.body,
        'action': notification.action,
        'metadata': notification.metadata,
        'category': notification.category,
        'severity': notification.severity,
        'is_read': notification.read_at is not None,
        'read_at': notification.read_at.isoformat() if notification.read_at else None,
        'dt_created': notification.dt_created.isoformat(),
    }


def sanitize_metadata(metadata: Any) -> Any:
    """
    The metadata without the keys that look like secrets (PRIVATE_METADATA_MARKERS), at
    any depth.
    """
    if isinstance(metadata, dict):
        return {
            key: sanitize_metadata(value)
            for key, value in metadata.items()
            if not any(marker in key.lower() for marker in PRIVATE_METADATA_MARKERS)
        }
    if isinstance(metadata, list):
        return [sanitize_metadata(value) for value in metadata]
    return metadata


def _recipients(users: Any) -> list:
    if users is None:
        return []
    if not isinstance(users, Iterable):
        users = [users]
    recipients = {}
    for user in users:
        if user is not None:
            recipients.setdefault(user.pk, user)
    return list(recipients.values())


def _render(text: Text, params: Mapping[str, Any] | None, user) -> str:
    if callable(text) and not isinstance(text, Promise):
        text = text(user)
    text = str(text)
    return text % params if params else text


def _jsonable(value: Any) -> Any:
    """
    The value as JSON stores it: lazy strings in the active language, dates as strings.
    """
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _has_channel(user) -> bool:
    # the user model has UserWsMixin of bazis-ws
    return callable(getattr(user, 'ws_publish', None))


def _publish(user, message: dict) -> None:
    try:
        user.ws_publish(message)
    except Exception:
        logger.exception('Notify: %s not published to the user %s', message['action'], user.pk)


def _publish_created(rows: list[Notification]) -> None:
    rows = [row for row in rows if _has_channel(row.user)]
    if not rows:
        return
    counts = dict(
        Notification.objects.filter(user_id__in={row.user_id for row in rows}, read_at__isnull=True)
        .order_by()
        .values_list('user')
        .annotate(count=Count('pk'))
    )
    for row in rows:
        _publish(
            row.user,
            {
                'action': 'notification_created',
                'notification': serialize(row),
                'unread_count': counts.get(row.user_id, 0),
            },
        )


def _publish_changed(user) -> None:
    _publish(user, {'action': 'notifications_changed', 'unread_count': unread_count(user)})


def _email(notification: Notification) -> EmailMessage:
    return EmailMessage(
        # a header is one line
        subject=' '.join(notification.title.split()),
        body=notification.body or notification.title,
        to=[notification.user.email],
    )


def _send_mail(messages: list[EmailMessage]) -> None:
    mailers.default.send_messages(messages)
