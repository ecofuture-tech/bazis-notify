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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import BooleanField, Case, Q, Value, When
from django.utils.translation import gettext_lazy as _

from bazis.core.models_abstract import JsonApiMixin, UuidMixin
from bazis.core.utils.orm import FieldAnnotate, calc_property

from .registry import UnknownNotificationTypeError, get_type


class Notification(UuidMixin, JsonApiMixin):
    """
    A notification of a user, created by `notify`. Only `read_at` changes after it is
    created (the client reads and unreads it); the texts are stored in the language of the
    user.
    """

    #: the fields a write may change
    MUTABLE_FIELDS = frozenset({'read_at'})

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_('Recipient'),
        on_delete=models.CASCADE,
        related_name='+',
    )
    #: the slug of a registered type (`bazis.contrib.notify.registry`); not `type`, which
    #: JSON:API reserves for the type of the resource
    notification_type = models.CharField(_('Type'), max_length=128)
    title = models.TextField(_('Title'))
    body = models.TextField(_('Text'), blank=True)
    #: where the client goes when the user opens the notification (`{"route": "..."}`)
    action = models.JSONField(_('Action'), default=dict, blank=True)
    #: data for the client, without secrets (`notify` drops the keys that look like ones)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    read_at = models.DateTimeField(_('Read at'), null=True, blank=True)
    #: a notification with the key of an existing notification of the user is not created
    dedupe_key = models.CharField(_('Deduplication key'), max_length=255, null=True, blank=True)
    dt_created = models.DateTimeField(_('Creation time'), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Notification')
        verbose_name_plural = _('Notifications')
        ordering = ['-dt_created']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'dedupe_key'],
                condition=Q(dedupe_key__isnull=False),
                name='notify_notification_user_dedupe_key',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'read_at'], name='notify_user_read_at'),
            models.Index(fields=['user', '-dt_created'], name='notify_user_dt_created'),
        ]

    def __str__(self):
        return self.title

    @calc_property(
        [
            FieldAnnotate(
                source='is_read',
                query=Case(
                    When(read_at__isnull=False, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
            )
        ],
        as_filter=True,
    )
    def is_read(self) -> bool:
        return self.read_at is not None

    @property
    def category(self) -> str:
        """
        The category of its type (the first part of the type if the type is no longer
        registered).
        """
        try:
            return get_type(self.notification_type).category
        except UnknownNotificationTypeError:
            return self.notification_type.split('.')[0]

    @property
    def severity(self) -> str:
        try:
            return get_type(self.notification_type).severity
        except UnknownNotificationTypeError:
            return 'info'

    def validate_item(self, changes) -> None:
        """
        A notification is not edited: a write after its creation changes only `read_at`.
        """
        if not changes.is_new and (changed := changes.fields - self.MUTABLE_FIELDS):
            raise ValidationError(
                {name: _('A notification cannot be changed') for name in sorted(changed)}
            )
