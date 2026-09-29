# Premium Telegram Bot Hosting v2

Included:
- Premium responsive hosting dashboard
- Upload Python bot + optional requirements.txt
- Run / stop / edit / backup / restore
- Improved process lifecycle cleanup
- Per-bot process locks to reduce duplicate starts
- 50 MB request-size cap
- Safer ZIP restore path validation
- Improved mobile UI
- Code editor with line numbers, Tab indentation, save/back controls

Important:
- No software can honestly guarantee that a bot will "never go down".
- This Flask app currently keeps child processes in memory; for production-grade 24/7 hosting,
  use a real process supervisor (systemd, Docker + restart policy, Supervisor, or a managed container platform),
  isolated environments per bot, resource limits, authentication/authorization, HTTPS, logging, and monitoring.
- requirements.txt installation should ideally be isolated per bot using a virtual environment rather than
  installing packages into the host Python environment.
