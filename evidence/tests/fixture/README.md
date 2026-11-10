# CP1 validation fixture

This **supporting workload is not the project deliverable**. It provides the smallest useful application interface for exercising ALB routing, EC2 initialization, Secrets Manager access, private RDS connectivity, and synthetic database writes.

- `GET /health`: HTTP 200 / `{"status":"ok"}` if Apache can reach Gunicorn (liveness only; it does **not** check RDS).
- `POST /api/contact`: HTTP 201 / `{"ok":true}` after a successful insert. Only use synthetic data and `example.com`, `example.org`, or `example.net` emails.
- `python -m evidence.tests.fixture.backend health`: operator-only Secrets Manager and RDS connectivity check.
- `python -m evidence.tests.fixture.backend probe`: operator-only synthetic write/read/delete diagnostic.

## Deployment contract

Apache serves `evidence/tests/fixture/site/` and proxies the two API routes to Gunicorn at `127.0.0.1:8000`. `deploy/apache.conf` and `deploy/resilient-infra.service` assume a checkout at `/opt/resilient-infra/`. Install dependencies from `requirements.txt`.

Supply `AWS_REGION`, `DB_SECRET_ID`, `DB_HOST`, `DB_NAME`, `DB_PORT`, and `DB_CA_BUNDLE` in `/etc/resilient-infra/app.env`, based on `deploy/app.env.example`. Credentials stay in Secrets Manager; the instance role needs narrowly scoped `secretsmanager:GetSecretValue`. The application uses a restricted database user, not the RDS master user. Database/schema and user creation are one-time initialization tasks, not Auto Scaling startup tasks. See `schema.sql`.

## Acceptance check

After deployment, run from the repository root:

```bash
python3 evidence/tests/smoke_test.py https://YOUR_CP1_DOMAIN
```

This tests the two public API routes; record separate infrastructure failure/recovery evidence. Application unit-test suites and the previous copy of the portfolio website are deliberately out of scope.
