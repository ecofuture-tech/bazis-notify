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
The notifications of the user, for the router module of the project
(BS_BAZIS_ROUTER_MODULE): `router.register('bazis.contrib.notify.router')`
(`/api/v1/notify/notification/` under `BazisRouter(prefix='/api/v1')`).
"""

from django.utils.translation import gettext_lazy as _

from bazis.core.routing import BazisRouter

from .routes import NotificationRouteSet


router = BazisRouter(tags=[_('Notifications')])
router.register(NotificationRouteSet.as_router())
