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
The notifications of the user through the API (`bazis.contrib.notify.router`).
"""

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from bazis_test_utils.utils import get_api_client

from bazis.contrib.notify.models import Notification
from bazis.contrib.notify.service import notify


URL = '/api/v1/notify/notification/'
TYPE = 'notify.notification'


def make_user(name):
    return get_user_model().objects.create_user(name, password=f'{name}-password-1')


def client_of(sample_app, user=None):
    return get_api_client(sample_app, user.jwt_build() if user else None)


def ids(response):
    assert response.status_code == 200, response.text
    return [item['id'] for item in response.json()['data']]


def read_at_body(pk, value):
    return {'data': {'id': str(pk), 'type': TYPE, 'attributes': {'read_at': value}}}


@pytest.fixture
def two_users():
    owner, stranger = make_user('owner'), make_user('stranger')
    own = [
        notify(owner, 'users.welcome', 'Welcome')[0],
        notify(owner, 'billing.purchase_declined', 'Declined', 'No credits')[0],
    ]
    other = notify(stranger, 'users.welcome', 'Welcome')[0]
    return owner, stranger, own, other


@pytest.mark.django_db(transaction=True)
def test_a_user_sees_only_his_notifications(sample_app, two_users):
    owner, stranger, own, other = two_users
    client = client_of(sample_app, owner)

    # the newest first
    assert ids(client.get(URL)) == [str(own[1].pk), str(own[0].pk)]
    item = client.get(f'{URL}{own[1].pk}/').json()['data']
    assert item['attributes'] == {
        'notification_type': 'billing.purchase_declined',
        'title': 'Declined',
        'body': 'No credits',
        'action': {},
        'metadata': {},
        'read_at': None,
        'dt_created': item['attributes']['dt_created'],
        'is_read': False,
        'category': 'billing',
        'severity': 'error',
    }
    # the recipient and the deduplication key are not shown
    assert 'relationships' not in item or 'user' not in item['relationships']

    for response in (
        client.get(f'{URL}{other.pk}/'),
        client.patch(f'{URL}{other.pk}/', json_data=read_at_body(other.pk, '2026-10-10T10:00:00Z')),
        client.delete(f'{URL}{other.pk}/'),
    ):
        assert response.status_code == 404, response.text
    assert Notification.objects.get(pk=other.pk).read_at is None
    assert ids(client_of(sample_app, stranger).get(URL)) == [str(other.pk)]


@pytest.mark.django_db(transaction=True)
def test_filters_and_pagination(sample_app, two_users):
    owner, _, own, _ = two_users
    Notification.objects.filter(pk=own[0].pk).update(read_at='2026-10-10T10:00:00Z')
    client = client_of(sample_app, owner)

    assert ids(client.get(URL, params={'filter': 'is_read=false'})) == [str(own[1].pk)]
    assert ids(client.get(URL, params={'filter': 'is_read=true'})) == [str(own[0].pk)]
    assert ids(client.get(URL, params={'filter': 'notification_type=users.welcome'})) == [
        str(own[0].pk)
    ]
    response = client.get(URL, params={'page[limit]': 1})
    assert ids(response) == [str(own[1].pk)]
    assert response.json()['links']['next']


@pytest.mark.django_db(transaction=True)
def test_a_request_without_a_user_is_unauthorized(sample_app, two_users):
    """
    Without a token or with an invalid one every route is 401.
    """
    owner, _, own, _ = two_users
    client = client_of(sample_app)
    item = f'{URL}{own[0].pk}/'

    for response in (
        client.get(URL),
        client.get(item),
        client.patch(item, json_data=read_at_body(own[0].pk, None)),
        client.delete(item),
        client.get(f'{URL}unread_count/'),
        client.post(f'{URL}read_all/'),
        client.post(f'{URL}clear/'),
    ):
        assert response.status_code == 401, response.text
    assert Notification.objects.filter(user=owner).count() == 2

    response = get_api_client(sample_app, 'a.b.c').get(URL)
    assert response.status_code == 401

    # the code of UserRequiredRouteBase, as for every route that requires a user
    operation = sample_app.openapi()['paths'][URL]['get']
    assert '401' in operation['responses'] and '403' not in operation['responses']
    assert operation['security'] == [{'OAuth2PasswordBearer': []}]


@pytest.mark.django_db(transaction=True)
def test_read_and_unread_one(sample_app, two_users, published):
    owner, _, own, _ = two_users
    client = client_of(sample_app, owner)
    item = f'{URL}{own[0].pk}/'

    response = client.patch(item, json_data=read_at_body(own[0].pk, '2026-10-10T10:00:00Z'))
    assert response.status_code == 200, response.text
    assert response.json()['data']['attributes']['is_read'] is True
    assert client.get(f'{URL}unread_count/').json() == {'unread_count': 1}
    assert published == [
        (owner.user_channel, {'action': 'notifications_changed', 'unread_count': 1})
    ]

    response = client.patch(item, json_data=read_at_body(own[0].pk, None))
    assert response.status_code == 200, response.text
    assert response.json()['data']['attributes']['is_read'] is False
    assert client.get(f'{URL}unread_count/').json() == {'unread_count': 2}
    assert published[-1] == (
        owner.user_channel,
        {'action': 'notifications_changed', 'unread_count': 2},
    )


@pytest.mark.django_db(transaction=True)
def test_only_read_at_is_written(sample_app, two_users):
    owner, _, own, _ = two_users
    client = client_of(sample_app, owner)
    body = {'data': {'id': str(own[0].pk), 'type': TYPE, 'attributes': {'title': 'Changed'}}}

    response = client.patch(f'{URL}{own[0].pk}/', json_data=body)

    assert response.status_code == 422, response.text
    assert Notification.objects.get(pk=own[0].pk).title == 'Welcome'
    create = {'data': {'type': TYPE, 'attributes': {'title': 'Forged'}}}
    assert client.post(URL, json_data=create).status_code == 405


@pytest.mark.django_db(transaction=True)
def test_delete_read_all_clear(sample_app, two_users, published):
    owner, stranger, own, other = two_users
    client = client_of(sample_app, owner)

    assert client.delete(f'{URL}{own[0].pk}/').status_code == 204
    assert ids(client.get(URL)) == [str(own[1].pk)]
    assert published[-1] == (
        owner.user_channel,
        {'action': 'notifications_changed', 'unread_count': 1},
    )

    notify(owner, 'users.welcome', 'Again')
    response = client.post(f'{URL}read_all/')
    assert response.status_code == 200, response.text
    assert response.json() == {'read': 2}
    assert client.get(f'{URL}unread_count/').json() == {'unread_count': 0}
    assert published[-1] == (
        owner.user_channel,
        {'action': 'notifications_changed', 'unread_count': 0},
    )
    # nothing left to read: no message
    count = len(published)
    assert client.post(f'{URL}read_all/').json() == {'read': 0}
    assert len(published) == count

    response = client.post(f'{URL}clear/')
    assert response.json() == {'deleted': 2}
    assert ids(client.get(URL)) == []

    # the notifications of the other user are untouched
    other = Notification.objects.get(pk=other.pk)
    assert other.read_at is None
    assert client_of(sample_app, stranger).get(f'{URL}unread_count/').json() == {
        'unread_count': 1
    }


@pytest.mark.django_db(transaction=True)
def test_the_server_sets_the_time_of_the_reading(sample_app, two_users):
    owner, _, own, _ = two_users
    client = client_of(sample_app, owner)
    item = f'{URL}{own[0].pk}/'

    before = timezone.now()
    response = client.patch(item, json_data=read_at_body(own[0].pk, '2030-01-01T00:00:00Z'))
    after = timezone.now()
    assert response.status_code == 200, response.text
    read_at = Notification.objects.get(pk=own[0].pk).read_at
    assert before <= read_at <= after
    assert response.json()['data']['attributes']['read_at'] is not None

    # a read notification keeps its time
    response = client.patch(item, json_data=read_at_body(own[0].pk, '2020-01-01T00:00:00Z'))
    assert response.status_code == 200, response.text
    assert Notification.objects.get(pk=own[0].pk).read_at == read_at

    # null unreads it, the next reading is now
    client.patch(item, json_data=read_at_body(own[0].pk, None))
    assert Notification.objects.get(pk=own[0].pk).read_at is None
    client.patch(item, json_data=read_at_body(own[0].pk, '2020-01-01T00:00:00Z'))
    assert Notification.objects.get(pk=own[0].pk).read_at > read_at
