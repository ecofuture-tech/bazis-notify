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

import json

from django.core.management import call_command

import pytest


@pytest.fixture(scope='session')
def django_db_setup(django_db_setup, django_db_blocker) -> None:
    with django_db_blocker.unblock():
        call_command('pgtrigger', 'install')


@pytest.fixture(scope='function')
def sample_app():
    from sample.main import app

    return app


@pytest.fixture
def published(monkeypatch):
    """
    The messages published to the channels of bazis-ws, instead of Redis.
    """
    from bazis.contrib.ws import models_abstract

    class DummyRedis:
        def __init__(self):
            self.messages = []

        def publish(self, channel, payload):
            self.messages.append((channel, json.loads(payload)))
            return 1

    redis = DummyRedis()
    monkeypatch.setattr(models_abstract, 'redis', redis)
    return redis.messages
