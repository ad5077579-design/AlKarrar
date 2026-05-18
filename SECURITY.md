# Security Policy

## Reporting vulnerabilities

If you discover a security issue (credential leak, auth bypass, unintended mainnet orders, etc.):

1. **Do not** open a public issue with exploit details.
2. Email or DM the maintainers privately (configure a `SECURITY_CONTACT` in repo settings when available).
3. Include steps to reproduce and impact assessment.

We aim to acknowledge within 7 days.

---

## Secrets handling

| Rule | Detail |
|------|--------|
| **Never commit** | `.env`, API keys, `data/*.db`, `data/*.json` with account data |
| **Use `.env.example`** | Placeholders only |
| **Keys source** | Trading uses `.env` via `EngineSettings` — not browser-localStorage for production |
| **Dashboard preview** | Last 4 characters of key only |

Before pushing:

```bash
git status
# ensure .env is not staged
```

---

## Operational security for operators

- Use **Spot Demo** for development ([demo.binance.com](https://demo.binance.com)).
- Restrict API keys: **Spot trading** only; IP whitelist when possible; no withdrawal permission.
- After switching demo → mainnet keys, verify:

```bash
python scripts/probe_binance_env.py --no-cache
```

- Clear stale grid snapshots or stop grids manually before mainnet (auto-resume disables mismatched snapshots, but best practice is a clean start).

---

## Dashboard authentication

Set **`ALKARRAR_DASHBOARD_PASSWORD`** in `.env` on the server (never in the browser). The Nuxt UI shows `/login`; the API issues an **HttpOnly** session cookie. Binance keys stay server-side.

| Variable | Role |
|----------|------|
| `ALKARRAR_DASHBOARD_PASSWORD` | Enables auth when non-empty |
| `ALKARRAR_DASHBOARD_USERNAME` | Default `admin` |
| `ALKARRAR_AUTH_SECRET` | Signs cookies (defaults to password — set a long random value in production) |
| `ALKARRAR_AUTH_COOKIE_SECURE=true` | Required when serving over HTTPS |

This is **single-operator** protection (not multi-user SaaS). For internet exposure, also use a reverse proxy (TLS, IP allowlist, VPN).

---

## Known limitations

- CORS allows localhost origins in dev — tighten for production deployments.
- SQLite files are local — protect server filesystem permissions.
- User stream may be unavailable on demo; balance relies on REST polling.
- Dashboard auth does not replace securing the host OS and firewall.

---

## Safe contribution areas

Documentation, UI copy, tests with mocks, and non-execution refactors are lower risk. Any change that places orders or moves funds requires review.

See [CONTRIBUTING.md](CONTRIBUTING.md).
