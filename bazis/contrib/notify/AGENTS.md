# bazis-notify — guide for AI agents

Stored notifications of the users: `notify` creates a row per recipient (the text in his
language), the client lists, reads and deletes his own notifications through the API, and
the new ones are delivered live to the open sessions (with bazis-ws) and by e-mail
(`email=True`). Use it for anything a user must see even if he was offline: reminders,
deadlines, results of background work, billing events.

## Setup

- `BS_INSTALLED_APPS` gets `bazis.contrib.notify` (a Django app: run `migrate`). It needs
  bazis-users (the user of the request, `UserLanguageMixin.language` for the texts).
- Register the API in the router module of the project (`BS_BAZIS_ROUTER_MODULE`):

  ```python
  router = BazisRouter(prefix='/api/v1')
  router.register('bazis.contrib.notify.router')   # /api/v1/notify/notification/
  ```

- Live delivery (optional): install `bazis-notify[ws]` (bazis-ws), give the user model
  `UserWsMixin` and register `bazis.contrib.ws.router` (see the guide of bazis-ws).
  Without them the notifications are stored (and e-mailed) only.
- Setting: `BS_BAZIS_NOTIFY_RETENTION_DAYS` (default empty: kept forever), see Retention.

## Notification types

Each notification has a registered type. Declare the types of an app in
`<app>/notifications.py` (imported when the apps are ready):

```python
from django.utils.translation import gettext_lazy as _
from bazis.contrib.notify.registry import register

register('billing.purchase_declined', title=_('Purchase declined'), severity='error',
         action={'route': '/settings/billing'})
register('training.deadline', title=_('Deadline soon'), severity='warning')
```

- The slug is lowercase words joined by dots; `category` defaults to its first part
  (`billing`), `severity` is `info` (default), `success`, `warning` or `error`, `action` is
  the default action of its notifications. The API shows `category` and `severity` from
  the type: no conditions on the type in the model or the client.
- `notify` with a type that is not registered raises `UnknownNotificationTypeError`; the same
  slug registered differently raises `ImproperlyConfigured`. A notification whose type is
  no longer registered is still listed (category: the first part of its type, `info`).

## Creating notifications

```python
from django.utils.translation import gettext_lazy as _
from bazis.contrib.notify.service import notify

# in hook_after_create / hook_after_update of a route, a transit, a task
notify(
    order.customer,                       # a user or an iterable (None and repeats skipped)
    'billing.purchase_declined',
    _('Payment for %(product)s declined'),   # title: a template, translated per recipient
    _('No credits were charged.'),           # body (optional)
    params={'product': order.product.name},
    action={'route': f'/orders/{order.pk}'},  # optional, the action of the type otherwise
    metadata={'order_id': str(order.pk)},     # optional data for the client
    dedupe_key=f'declined:{order.pk}',        # optional: at most one per user and key
    email=True,                               # optional: also by e-mail
)
```

- The texts are rendered for each recipient inside `translation.override(user.language or
  LANGUAGE_CODE)`: a lazy string is translated there, then formatted with `params` (`%`);
  a function `lambda user: ...` is called with the user. Do not format a lazy string
  before the call (`_('...') % {...}` is translated in the language of the request).
  The rows keep the rendered texts.
- `notify` returns the notifications it created. With `dedupe_key`, a user who already has
  a notification with that key gets none (and no e-mail): a retried call or task does not
  notify twice. One insert for all the recipients.
- `action` and `metadata` are JSON (lazy strings and dates are converted). The keys of
  `metadata` containing `token`, `secret`, `password`, `receipt`, `authorization`,
  `api_key`, `apikey`, `credential`, `cookie` or `traceback` are dropped at any depth; the
  user sees the metadata, never put private data there.
- A notification is not edited: a write that changes anything but `read_at` fails
  (`validate_item`). Delete it and create another.
- The delivery waits for the commit of the current transaction (at once outside one): a
  rolled back write delivers nothing.

### Every user (fan-out)

`notify_all(users, type, title, body, batch_size=500, **kwargs)` takes a queryset (read
with `iterator()`) or an iterable and calls `notify` per batch: one insert and a constant
number of queries per batch, whatever the number of users. Run it from a task of bazis-bg
outside a transaction, so that each batch commits and is delivered on its own; with a
`dedupe_key`, a retried task skips the users already notified:

```python
from bazis.contrib.bg.basic.base import BgBase
from bazis.contrib.notify.service import notify_all

class Announce(BgBase):
    name = 'Announce a release'
    is_transaction = False

    def __init__(self, release_id):
        self.release_id = release_id

    def handle(self):
        users = User.objects.filter(is_active=True)
        self.set_result({'created': notify_all(
            users, 'product.release', _('Version %(version)s is out'),
            params={'version': ...}, dedupe_key=f'release:{self.release_id}')})
```

Not one task per user, and no `exists()` per user: the deduplication key does that.

## Live delivery (bazis-ws)

After the commit, to the channel of each recipient whose user model has `UserWsMixin` (the
message arrives as `{"type": "data", "data": "<JSON>"}`, see bazis-ws):

- `{"action": "notification_created", "notification": {...}, "unread_count": N}`: the new
  notification with the attributes of its resource (`id`, `notification_type`, `title`,
  `body`, `action`, `metadata`, `category`, `severity`, `is_read`, `read_at`,
  `dt_created`) and the unread count of the user;
- `{"action": "notifications_changed", "unread_count": N}`: after a read, an unread, a
  delete (one or `clear`) or `read_all` of the user (through the API or `read_all`/`clear`
  of the service); the client refetches its list.

A failed publish (Redis down) is logged (`bazis.contrib.notify.service`) and does not fail
the write; the notification stays stored. A client fetches the unread notifications when
it (re)connects: the socket delivers only while it is connected.

## E-mail

`email=True` sends each created notification to `user.email` (users without one are
skipped): the subject is the title (on one line), the text the body (the title without a
body), in the language of the user, from `DEFAULT_FROM_EMAIL`. The messages of one
`notify` call go after the commit in one batch through the default mailer of the project,
which the core configures from the dynamic settings `BAZIS_EMAIL_BACKEND` (default
`bazis.core.mail.DynamicSMTPEmailBackend`), `BAZIS_EMAIL_HOST`, `BAZIS_EMAIL_PORT`,
`BAZIS_EMAIL_HOST_USER`, `BAZIS_EMAIL_HOST_PASSWORD`, `BAZIS_EMAIL_USE_TLS`,
`BAZIS_EMAIL_USE_SSL` (`BS_BAZIS_EMAIL_*`, editable in the admin) and `DEFAULT_FROM_EMAIL`.
A failure of the mail server is logged (Django logs the failed `on_commit` callback) and
does not fail the write. The sending runs in the process of the write: send to many users
from a task (`notify_all`), not in a request. Tests get the messages in `mail.outbox`
(Django uses the locmem backend in tests).

## API

`/api/v1/notify/notification/` (the resource `notify.notification`), only the
notifications of the user of the request; another user's notification is 404, an
anonymous request 403 (`ERR_FORBIDDEN`), an invalid token 401.

- `GET /`: the list, newest first, with the JSON:API filters and pagination:
  `filter=is_read=false` (the unread ones, e.g. on (re)connect), `filter=notification_type=billing.purchase_declined`,
  `page[limit]`, `page[offset]`. `GET /{id}/`: one notification.
- `PATCH /{id}/` with `{"data": {"id", "type": "notify.notification", "attributes":
  {"read_at": "<time>"}}}` marks it read, `"read_at": null` unread; any other attribute is
  422. `DELETE /{id}/` deletes it. No create: notifications come from `notify`.
- `GET /unread_count/` → `{"unread_count": N}`; `POST /read_all/` → `{"read": N}`;
  `POST /clear/` (deletes all) → `{"deleted": N}`.
- Attributes: `notification_type`, `title`, `body`, `action`, `metadata`, `read_at`,
  `dt_created`, `is_read`, `category`, `severity`. The recipient and the deduplication key
  are not shown.

## Retention

`delete_expired(days=None)` deletes the notifications created more than
`BAZIS_NOTIFY_RETENTION_DAYS` days ago (read or not; `days` overrides the setting; nothing
without either) and returns their number. Run it daily from a periodic task of bazis-bg
(a `BgBase` whose `handle` calls it, with a `TaskCron` such as `"0 3 * * *"`).

## Rules

- Create notifications only with `notify`/`notify_all` (types, rendering, deduplication,
  delivery); not with `Notification.objects.create`.
- Notify only the users who may see what the notification says.
- Not from `validate_item`: it may run more than once for one write; use the hooks of the
  write, a transit or a task.
- `bazis.contrib.ws.messages.notify` of bazis-ws stays a live message without storage
  (lost if the user is not connected); use this package for what the user must find later.
