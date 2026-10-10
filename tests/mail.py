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
A mail backend of the tests that refuses some recipients, as an SMTP server does.
"""

from smtplib import SMTPRecipientsRefused

from django.core.mail.backends.locmem import EmailBackend


class RefusingBackend(EmailBackend):
    """
    The locmem backend that refuses the addresses starting with `refused`.
    """

    def send_messages(self, messages):
        for message in messages:
            refused = [to for to in message.to if to.startswith('refused')]
            if refused:
                raise SMTPRecipientsRefused({to: (550, b'No such user') for to in refused})
        return super().send_messages(messages)
