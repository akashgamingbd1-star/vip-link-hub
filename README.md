# NovaHost — Premium Telegram Bot Hosting Starter

## Included
- Premium responsive landing page
- User account creation with generated UID
- UID login
- Referral links and referral relationship storage
- Hosting packages
- User dashboard
- Bot records and package limits
- Separate admin console at `/admin`
- Admin UID/name search and balance editing
- Order management
- SQLite database

## Run
1. Install Node.js 18+.
2. Open this folder in a terminal.
3. Run `npm install`.
4. Copy `.env.example` to `.env` and set a strong `SESSION_SECRET`, admin username and password.
5. Run `npm start`.
6. Open `http://localhost:3000`
7. Admin console: `http://localhost:3000/admin`

## Important production pieces
This starter deliberately does not pretend that a frontend can actually host Telegram bots or collect real payments by itself.

For production:
- Add a real payment gateway (for Bangladesh, use the gateway/account you actually have access to).
- Add payment webhook verification before marking orders as paid.
- Add authentication hardening, HTTPS, rate limiting, CSRF protection and secure session storage.
- Connect the bot deployment API to Docker/systemd/PM2 or a separate worker service.
- Never execute arbitrary uploaded code directly on the web server.
- Store bot secrets encrypted and isolate each customer's process/container.
- Add subscription expiry, renewals, resource monitoring, logs and automated suspension.
- Configure a real domain and reverse proxy.

The UI and database structure are designed so those production services can be added without rebuilding the whole frontend.
