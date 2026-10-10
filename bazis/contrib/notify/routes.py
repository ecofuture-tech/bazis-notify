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

from django.apps import apps
from django.db.models import QuerySet
from django.utils import timezone

from starlette.status import HTTP_403_FORBIDDEN

from pydantic import BaseModel

from bazis.contrib.users.models_abstract import UserMixin
from bazis.contrib.users.routes_abstract import UserRouteBase
from bazis.core.errors import JsonApi403Exception, SchemaErrors
from bazis.core.routes_abstract.initial import http_get, http_post
from bazis.core.routes_abstract.jsonapi import RestrictedQsRouteMixin
from bazis.core.schemas import AccessAction, CrudAccessAction, CrudApiAction, SchemaFields
from bazis.core.schemas.enums import RouteKind

from . import service


class UnreadCount(BaseModel):
    unread_count: int


class ReadAllResult(BaseModel):
    #: the notifications marked read
    read: int


class ClearResult(BaseModel):
    #: the notifications deleted
    deleted: int


class NotificationRouteSet(RestrictedQsRouteMixin, UserRouteBase):
    """
    The notifications of the user of the request: the list (filters by
    `notification_type`, `is_read`), an item, PATCH `read_at` (a time marks it read, null
    unread; the time is the server's), DELETE, and the collection actions `unread_count`,
    `read_all` and `clear`. Nobody sees or changes the notifications of another user (404); an anonymous request
    is refused (403). The notifications are created by `notify`, not through the API.
    """

    model = apps.get_model('notify.Notification')
    # the token is required: OpenAPI declares it so (the anonymous user gets 403)
    auth_required = True
    actions_exclude = [
        'action_create',
        'action_schema_create',
        'action_post_relationships',
        'action_update_relationships',
        'action_delete_relationships',
    ]

    fields = {
        None: SchemaFields(
            origin={
                'notification_type': None,
                'title': None,
                'body': None,
                'action': None,
                'metadata': None,
                'read_at': None,
                'dt_created': None,
            },
            include={'is_read': None, 'category': None, 'severity': None},
        ),
        CrudApiAction.UPDATE: SchemaFields(origin={'read_at': None}),
    }

    @classmethod
    def route_responses(cls, route_ctx):
        return {**super().route_responses(route_ctx), HTTP_403_FORBIDDEN: {'model': SchemaErrors}}

    @classmethod
    def restrict_queryset(
        cls, qs: QuerySet, access_action: AccessAction, user=None, **kwargs
    ) -> QuerySet:
        """
        The notifications of the user (without one, the authenticated user of the request);
        none for an anonymous user.
        """
        if user is None:
            user = UserMixin.CTX_USER_REQUEST.get()
        if user is None or user.is_anonymous:
            return qs.none()
        return super().restrict_queryset(qs, access_action, user=user, **kwargs).filter(user=user)

    def get_queryset(self):
        return self.restrict_queryset(
            super().get_queryset(), CrudAccessAction.VIEW, user=self.inject.user
        )

    def route_run(self, *args, **kwargs):
        if getattr(self.inject.user, 'is_anonymous', True):
            raise JsonApi403Exception
        return super().route_run(*args, **kwargs)

    def hook_before_update(self, item):
        super().hook_before_update(item)
        self.read_at_stored = item.read_at

    def hook_after_update(self, item):
        """
        The time of the reading is the server's: any `read_at` the client sends marks the
        notification read now (a read one keeps its time), null marks it unread.
        """
        super().hook_after_update(item)
        if item.read_at is not None:
            read_at = self.read_at_stored or timezone.now()
            if item.read_at != read_at:
                item.read_at = read_at
                item.save(update_fields=['read_at'])
        service.notifications_changed(self.inject.user)

    def destroy(self, item_id: str):
        result = super().destroy(item_id)
        service.notifications_changed(self.inject.user)
        return result

    @http_get('/unread_count/', response_model=UnreadCount, kind=RouteKind.OTHER)
    def action_unread_count(self, **kwargs):
        return UnreadCount(unread_count=service.unread_count(self.inject.user))

    @http_post('/read_all/', response_model=ReadAllResult, kind=RouteKind.OTHER)
    def action_read_all(self, **kwargs):
        return ReadAllResult(read=service.read_all(self.inject.user))

    @http_post('/clear/', response_model=ClearResult, kind=RouteKind.OTHER)
    def action_clear(self, **kwargs):
        return ClearResult(deleted=service.clear(self.inject.user))
