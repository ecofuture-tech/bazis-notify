# Bazis Notify

Stored notifications of the users for the Bazis framework: an inbox API of the user's own
notifications, live delivery of the new ones over [bazis-ws](https://github.com/ecofuture-tech/bazis-ws)
and an optional e-mail channel through the mail backend of the Bazis core.

## Quick Start

```bash
uv add 'bazis-notify[ws]'   # without [ws]: stored and e-mailed only
```

```python
# project.env: BS_INSTALLED_APPS='[..., "bazis.contrib.notify"]', then migrate

# the router module of the project (BS_BAZIS_ROUTER_MODULE)
router = BazisRouter(prefix='/api/v1')
router.register('bazis.contrib.ws.router')       # live delivery (optional)
router.register('bazis.contrib.notify.router')   # /api/v1/notify/notification/

# <app>/notifications.py: the notification types of the app
from bazis.contrib.notify.registry import register

register('billing.purchase_declined', title=_('Purchase declined'), severity='error',
         action={'route': '/settings/billing'})

# in the hooks of a write, a transit or a background task
from bazis.contrib.notify.service import notify

notify(order.customer, 'billing.purchase_declined',
       _('Payment for %(product)s declined'), _('No credits were charged.'),
       params={'product': order.product.name}, dedupe_key=f'declined:{order.pk}', email=True)
```

## What it does

- **Storage**: one `Notification` per recipient (`notification_type`, `title`, `body`,
  `action`, `metadata`, `read_at`, `dedupe_key`, `dt_created`), immutable except
  `read_at`; texts rendered in the language of each recipient; metadata without secrets;
  at most one notification per user and `dedupe_key`.
- **Types**: registered by the apps (`slug`, `category`, `severity`, default `action`);
  an unknown type is refused.
- **API** (own notifications only, 401 without a valid token): list with `filter=is_read=false`,
  `filter=notification_type=...` and pagination, retrieve, `PATCH read_at` (read / unread, the time is the server's),
  `DELETE`, `GET unread_count/`, `POST read_all/`, `POST clear/`.
- **Live**: with bazis-ws, `notification_created` and `notifications_changed` (with the
  unread count) on the channel of the user after the commit.
- **E-mail**: `email=True` sends after the commit, one connection per call (a refused address is logged, the others are sent), through the default
  mailer configured by `BAZIS_EMAIL_*`.
- **Fan-out**: `notify_all(queryset, ...)` in batches from a bazis-bg task, idempotent with
  a `dedupe_key`.
- **Retention**: `BAZIS_NOTIFY_RETENTION_DAYS` and `delete_expired()` for a periodic task.

The full guide is [`bazis/contrib/notify/AGENTS.md`](bazis/contrib/notify/AGENTS.md).

## License

Apache License 2.0.
