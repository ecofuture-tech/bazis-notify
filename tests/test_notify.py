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
Creating the notifications (`bazis.contrib.notify.service`) and the type registry.
"""

from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy

import pytest

from bazis.contrib.notify import service
from bazis.contrib.notify.models import Notification
from bazis.contrib.notify.registry import UnknownNotificationTypeError, get_type, register
from bazis.contrib.notify.service import delete_expired, notify, notify_all
from bazis.core.errors import JsonApiItemInvalidException


#: messages of the Django catalogs, translated to Russian
REQUIRED = gettext_lazy('This field is required.')
REQUIRED_RU = 'Обязательное поле.'
#: a message with a placeholder
NOT_INTEGER = gettext_lazy('“%(value)s” value must be an integer.')


def make_user(name, language=None, email=''):
    return get_user_model().objects.create_user(
        name, password=f'{name}-password-1', language=language, email=email
    )


def test_registry():
    welcome = get_type('users.welcome')
    assert (welcome.category, welcome.severity, welcome.action) == (
        'users',
        'success',
        {'route': '/profile'},
    )
    # the same declaration again (a module imported twice) is the registered type
    assert register(
        'users.welcome', title=gettext_lazy('Welcome'), severity='success',
        action={'route': '/profile'},
    ) is welcome

    with pytest.raises(ImproperlyConfigured):
        register('users.welcome', severity='warning')
    with pytest.raises(ImproperlyConfigured):
        register('users.other', severity='fatal')
    with pytest.raises(ImproperlyConfigured):
        register('Users Welcome')
    with pytest.raises(UnknownNotificationTypeError):
        get_type('users.unknown')


@pytest.mark.django_db(transaction=True)
def test_unknown_type_is_refused():
    user = make_user('user')

    with pytest.raises(UnknownNotificationTypeError):
        notify(user, 'users.unknown', 'Title')

    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_texts_in_the_language_of_each_recipient():
    ru_user, en_user, default_user = make_user('ru', 'ru'), make_user('en', 'en'), make_user('x')

    rows = notify(
        [ru_user, en_user, default_user, None, ru_user],
        'users.welcome',
        REQUIRED,
        NOT_INTEGER,
        params={'value': 'x'},
    )

    assert len(rows) == 3
    texts = {
        row.user.username: (row.title, row.body)
        for row in Notification.objects.select_related('user')
    }
    with translation.override('ru'):
        body_ru = str(NOT_INTEGER) % {'value': 'x'}
    body_en = '“x” value must be an integer.'
    assert body_ru == 'Значение “x” должно быть целым числом.'
    assert texts == {
        'ru': (REQUIRED_RU, body_ru),
        'en': ('This field is required.', body_en),
        # without a language: LANGUAGE_CODE
        'x': ('This field is required.', body_en),
    }

    # a function of the recipient
    notify([ru_user, en_user], 'users.welcome', lambda user: f'Hello, {user.username}')
    assert set(
        Notification.objects.filter(title__startswith='Hello').values_list('title', flat=True)
    ) == {'Hello, ru', 'Hello, en'}


@pytest.mark.django_db(transaction=True)
def test_action_and_metadata():
    user = make_user('user')
    moment = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    default, = notify(
        user,
        'users.welcome',
        'Hi',
        metadata={
            'order': 7,
            'at': moment,
            'label': gettext_lazy('Welcome'),
            'api_token': 'x',
            'Authorization': 'Bearer x',
            'nested': {'Password': 'p', 'ok': 2},
            'items': [{'client_secret': 1, 'name': 'a'}],
            'receipt_data': 'r',
        },
    )
    explicit, = notify(user, 'users.welcome', 'Hi', action={'route': '/orders/7'})
    none, = notify(user, 'billing.purchase_declined', 'Declined')

    default.refresh_from_db()
    assert default.action == {'route': '/profile'}
    assert default.metadata == {
        'order': 7,
        'at': '2026-10-10T12:00:00Z',
        'label': 'Welcome',
        'nested': {'ok': 2},
        'items': [{'name': 'a'}],
    }
    explicit.refresh_from_db()
    assert explicit.action == {'route': '/orders/7'}
    none.refresh_from_db()
    assert (none.action, none.metadata) == ({}, {})
    assert (none.category, none.severity) == ('billing', 'error')


@pytest.mark.django_db(transaction=True)
def test_dedupe_key_notifies_once():
    first, second, third = make_user('first', email='first@example.com'), make_user(
        'second', email='second@example.com'
    ), make_user('third', email='third@example.com')

    rows = notify([first, second], 'users.welcome', 'Hi', dedupe_key='welcome', email=True)
    assert {row.user for row in rows} == {first, second}

    rows = notify(
        [first, second, third], 'users.welcome', 'Hi', dedupe_key='welcome', email=True
    )
    assert [row.user for row in rows] == [third]
    assert Notification.objects.count() == 3
    # no second e-mail for a duplicate
    assert sorted(message.to[0] for message in mail.outbox) == [
        'first@example.com',
        'second@example.com',
        'third@example.com',
    ]
    # another key is another notification
    assert len(notify(first, 'users.welcome', 'Hi', dedupe_key='other')) == 1
    # without a key, nothing is deduplicated
    assert len(notify(first, 'users.welcome', 'Hi')) == 1
    assert len(notify(first, 'users.welcome', 'Hi')) == 1


@pytest.mark.django_db(transaction=True)
def test_notify_all_in_batches():
    for number in range(10):
        make_user(f'user{number:02}')
    users = get_user_model().objects.order_by('username')

    with CaptureQueriesContext(connection) as queries:
        assert notify_all(users[:5], 'users.welcome', 'News', batch_size=2, dedupe_key='a') == 5
    inserts = [q for q in queries.captured_queries if q['sql'].startswith('INSERT')]
    assert len(inserts) == 3

    # the queries of a batch do not depend on the number of its users
    with CaptureQueriesContext(connection) as five:
        notify_all(users[:5], 'users.welcome', 'News', batch_size=5, dedupe_key='b')
    with CaptureQueriesContext(connection) as ten:
        notify_all(users, 'users.welcome', 'News', batch_size=10, dedupe_key='c')
    assert len(ten.captured_queries) == len(five.captured_queries)

    # a retried run skips the users already notified
    assert notify_all(users, 'users.welcome', 'News', batch_size=3, dedupe_key='a') == 5
    assert notify_all(users, 'users.welcome', 'News', batch_size=3, dedupe_key='a') == 0
    assert Notification.objects.filter(dedupe_key='a').count() == 10


@pytest.mark.django_db(transaction=True)
def test_email_after_the_commit_in_the_language_of_the_user():
    ru_user = make_user('ru', 'ru', email='ru@example.com')
    en_user = make_user('en', 'en', email='en@example.com')
    no_email = make_user('nobody')

    with transaction.atomic():
        notify(
            [ru_user, en_user, no_email],
            'users.welcome',
            REQUIRED,
            'Line one\nline two',
            email=True,
        )
        assert mail.outbox == []

    assert sorted((m.to, m.subject, m.body) for m in mail.outbox) == [
        (['en@example.com'], 'This field is required.', 'Line one\nline two'),
        (['ru@example.com'], REQUIRED_RU, 'Line one\nline two'),
    ]

    # a rolled back write sends nothing
    mail.outbox.clear()
    with pytest.raises(RuntimeError), transaction.atomic():
        notify(ru_user, 'users.welcome', 'Rolled back', email=True)
        raise RuntimeError
    assert mail.outbox == []
    assert not Notification.objects.filter(title='Rolled back').exists()

    # the subject is one line; without email=True nothing is sent
    notify(en_user, 'users.welcome', 'Two\nlines', email=True)
    notify(en_user, 'users.welcome', 'Quiet')
    assert [m.subject for m in mail.outbox] == ['Two lines']


@pytest.mark.django_db(transaction=True)
def test_email_failure_does_not_fail_the_write(monkeypatch, caplog):
    user = make_user('user', email='user@example.com')

    def broken(messages):
        raise ConnectionRefusedError('SMTP down')

    monkeypatch.setattr(service, '_send_mail', broken)
    with transaction.atomic():
        notify(user, 'users.welcome', 'Hi', email=True)

    assert Notification.objects.filter(user=user).count() == 1
    assert 'SMTP down' in caplog.text


@pytest.mark.django_db(transaction=True)
def test_new_notifications_published_after_the_commit(published):
    first, second = make_user('first'), make_user('second')
    notify(first, 'users.welcome', 'Earlier')
    published.clear()

    with transaction.atomic():
        rows = notify([first, second], 'billing.purchase_declined', 'Declined', 'No credits')
        assert published == []

    by_channel = dict(published)
    assert set(by_channel) == {first.user_channel, second.user_channel}
    message = by_channel[first.user_channel]
    row = next(row for row in rows if row.user == first)
    assert message == {
        'action': 'notification_created',
        'notification': {
            'id': str(row.pk),
            'notification_type': 'billing.purchase_declined',
            'title': 'Declined',
            'body': 'No credits',
            'action': {},
            'metadata': {},
            'category': 'billing',
            'severity': 'error',
            'is_read': False,
            'read_at': None,
            'dt_created': row.dt_created.isoformat(),
        },
        'unread_count': 2,
    }
    assert by_channel[second.user_channel]['unread_count'] == 1

    # a rolled back write publishes nothing
    published.clear()
    with pytest.raises(RuntimeError), transaction.atomic():
        notify(first, 'users.welcome', 'Rolled back')
        raise RuntimeError
    assert published == []


@pytest.mark.django_db(transaction=True)
def test_publish_failure_is_logged(monkeypatch, caplog):
    from bazis.contrib.ws import models_abstract

    class DownRedis:
        def publish(self, channel, payload):
            raise ConnectionError('Redis down')

    monkeypatch.setattr(models_abstract, 'redis', DownRedis())
    user = make_user('user')

    rows = notify(user, 'users.welcome', 'Hi')

    assert len(rows) == 1
    assert Notification.objects.filter(user=user).exists()
    assert 'notification_created not published' in caplog.text
    assert 'Redis down' in caplog.text


@pytest.mark.django_db(transaction=True)
def test_notification_is_immutable_except_read_at():
    user = make_user('user')
    row, = notify(user, 'users.welcome', 'Hi')
    row = Notification.objects.get(pk=row.pk)

    row.read_at = timezone.now()
    row.save()

    row = Notification.objects.get(pk=row.pk)
    row.title = 'Changed'
    with pytest.raises(JsonApiItemInvalidException):
        row.save()
    assert Notification.objects.get(pk=row.pk).title == 'Hi'


@pytest.mark.django_db(transaction=True)
def test_delete_expired(settings):
    user = make_user('user')
    old, recent = notify(user, 'users.welcome', 'Old')[0], notify(user, 'users.welcome', 'Recent')[0]
    Notification.objects.filter(pk=old.pk).update(dt_created=timezone.now() - timedelta(days=31))

    # no retention: nothing is deleted
    assert settings.BAZIS_NOTIFY_RETENTION_DAYS is None
    assert delete_expired() == 0

    settings.BAZIS_NOTIFY_RETENTION_DAYS = 30
    assert delete_expired() == 1
    assert list(Notification.objects.values_list('pk', flat=True)) == [recent.pk]

    # an explicit number of days wins over the setting
    assert delete_expired(days=60) == 0


@pytest.mark.django_db(transaction=True)
def test_returned_rows_are_ordinary_instances():
    user = make_user('user')
    row, = notify(user, 'users.welcome', 'Hi')

    assert row.user is user
    row.read_at = timezone.now()
    row.save()
    assert Notification.objects.get(pk=row.pk).read_at == row.read_at

    row.title = 'Changed'
    with pytest.raises(JsonApiItemInvalidException):
        row.save()


@pytest.mark.django_db(transaction=True)
def test_params_format_only_templates():
    user = make_user('a%b')

    row, = notify(
        user,
        'users.welcome',
        lambda recipient: f'Hello, {recipient.username} (100%)',
        NOT_INTEGER,
        params={'value': '50%'},
    )

    assert (row.title, row.body) == ('Hello, a%b (100%)', '“50%” value must be an integer.')


@pytest.mark.django_db(transaction=True)
def test_a_refused_email_does_not_stop_the_others(settings, caplog):
    settings.MAILERS = {'default': {'BACKEND': 'tests.mail.RefusingBackend'}}
    users = [
        make_user('first', email='first@example.com'),
        make_user('refused', email='refused@example.com'),
        make_user('last', email='last@example.com'),
    ]

    rows = notify(users, 'users.welcome', 'Hi', email=True)

    assert sorted(m.to[0] for m in mail.outbox) == ['first@example.com', 'last@example.com']
    refused = next(row for row in rows if row.user.username == 'refused')
    assert f'the e-mail of the notification {refused.pk} to the user {users[1].pk} not sent' in (
        caplog.text
    )
    assert 'No such user' in caplog.text
    assert Notification.objects.count() == 3


@pytest.mark.django_db(transaction=True)
def test_delete_expired_in_batches():
    user = make_user('user')
    for number in range(5):
        notify(user, 'users.welcome', f'Old {number}')
    kept, = notify(user, 'users.welcome', 'Recent')
    Notification.objects.exclude(pk=kept.pk).update(dt_created=timezone.now() - timedelta(days=10))

    with CaptureQueriesContext(connection) as queries:
        assert delete_expired(days=7, batch_size=2) == 5
    deletes = [q for q in queries.captured_queries if q['sql'].startswith('DELETE')]
    assert len(deletes) == 3
    assert list(Notification.objects.values_list('pk', flat=True)) == [kept.pk]


@pytest.mark.django_db(transaction=True)
def test_duplicate_is_not_published(published):
    user = make_user('user')
    notify(user, 'users.welcome', 'Hi', dedupe_key='once')
    assert len(published) == 1

    assert notify(user, 'users.welcome', 'Hi', dedupe_key='once') == []
    assert len(published) == 1


@pytest.mark.django_db(transaction=True)
def test_user_model_without_a_channel(monkeypatch, published, caplog):
    # a user model without UserWsMixin has no ws_publish
    monkeypatch.setattr(get_user_model(), 'ws_publish', None)
    user = make_user('user')

    rows = notify(user, 'users.welcome', 'Hi')
    assert service.read_all(user) == 1
    assert service.clear(user) == 1

    assert len(rows) == 1
    assert published == []
    assert 'not published' not in caplog.text
