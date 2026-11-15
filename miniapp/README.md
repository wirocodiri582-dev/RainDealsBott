# RainDeals Telegram Mini App

The customer Mini App is served by the existing FastAPI service at `/miniapp/`.
It shares the bot's database and checkout code; it does not create a second
catalog, wallet, or order system.

## Render configuration

Set this environment value on the Render service that runs `render_api.py`:

```text
MINI_APP_URL=https://<your-render-service-host>/miniapp
```

The main bot username is read from Telegram at startup. `BOT_TOKEN` is used
only on the server to verify Telegram's signed `initData`; it is never returned
to the browser. Deploy the existing service after adding the URL, then register
the HTTPS host for the bot's Mini App in BotFather. The bot home menu will show
the RainDeals Mini App button when `MINI_APP_URL` is configured.

## Screens

- Store: paid products, separate Freebies view, category chips, search, stock
  filter, sorting, product detail, and cart.
- Balance: current wallet and recent wallet entries. Deposits continue through
  the existing Telegram flow.
- Orders and notifications: only the signed-in Telegram user's records.
- API: active-key count and documentation link. The Mini App never reveals the
  API key; key management remains in the bot.
- Profile: Telegram account and store activity summary.

The cart uses the existing server-side stock/price review and idempotent order
checkout. It collects configured email, phone, and text fields before wallet
checkout. Product requirements for a photo or Telegram contact stay in the bot,
where Telegram can validate and attach those inputs.

## Security

Every API request must include Telegram `initData`. The server checks its HMAC,
timestamp, and stored account status. Product, cart, wallet, notification, and
order queries are scoped to that verified Telegram ID. Orders expose status in
the Mini App; delivered credentials remain in the private Telegram order card.
