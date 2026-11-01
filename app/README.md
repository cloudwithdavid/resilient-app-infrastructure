# Supporting application

This demo serves a copy of the Cloud With David website using Apache and a Python API backed by MySQL. The contact form is for **synthetic data only**: entries are stored in the database, not emailed or monitored. It accepts reserved `example.com`, `example.org`, and `example.net` email domains. Do not enter real personal information. There is no public read endpoint. The site copy comes from `cloudwithdavid/cloudwithdavid-site` commit `dbc209446600a051fdcf6ad7f5800fff8e5ccc51`; the production repository and website are independent. Cloudflare's contact service, challenge, and analytics scripts are absent from this copy.

- Apache serves `app/site` and proxies `/health` and `/api/contact` to Gunicorn on loopback. The API accepts JSON fields `name`, `email`, `subject`, `message`, and optional empty `honeypot`; maximum request size is 16 KiB. A successful insert returns HTTP 201 and `{"ok":true}`. Bad input returns 400; unavailable dependencies or exhausted write slots return 503. SQL values are parameterized and errors do not include request data or credentials.
- **Configure the ALB target group to use `GET /health`, with success code 200.** It checks that Apache can reach a responding Python worker. It does not contact Secrets Manager or RDS, validate configuration, test the schema, or guarantee a write will succeed. It remains healthy during downstream failures; monitor contact API 5xx and perform a separate controlled dependency check to detect those failures. Do not use the dependency diagnostic as an ALB or Auto Scaling health check.
- `python -m app.backend health` is the separate, operator-run dependency check. It retrieves the secret and executes `SELECT 1` over verified TLS, but does not validate write permissions. `python -m app.backend probe` additionally inserts, reads, and deletes its own synthetic row. If cleanup fails, it reports the UUID for an administrator to inspect and remove. Neither diagnostic is exposed by Apache.

Gunicorn runs two workers with four threads each, with at most two concurrent contact requests per worker. Excess writes receive 503, keeping threads available for liveness during slow dependency calls. The limit is per worker, so distribution can be uneven; it is an availability guard, not a traffic or abuse control. WAF rate rules remain an infrastructure responsibility. Extreme traffic, host resource exhaustion, or a broken Apache/Python process can still make liveness fail. Follow [AWS target health-check guidance](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html) and configure a startup grace period appropriate to the instance boot process.

## Python setup and tests

Python 3.10 or newer is required. From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
node --check app/site/js/main.js
node tests/test_frontend.js
```

The normal suite uses simulated AWS/database responses and skips the optional database and Apache integration tests. To run those tests, use a **disposable local MySQL-compatible server**, with TLS for `localhost`, a CA certificate, an administrative root account with Unix-socket access, and an isolated database named `resilient_test`. For example, in the local server's administrative SQL client:

```sql
CREATE DATABASE resilient_test CHARACTER SET utf8mb4;
CREATE USER 'resilient_test'@'127.0.0.1' IDENTIFIED BY 'local-test-only' REQUIRE SSL;
-- Apply app/schema.sql to resilient_test before issuing these grants.
GRANT INSERT ON resilient_test.contact_submissions TO 'resilient_test'@'127.0.0.1';
GRANT SELECT, INSERT, DELETE ON resilient_test.connectivity_probe TO 'resilient_test'@'127.0.0.1';
```

Use a server configured to bind `127.0.0.1` with name resolution disabled for this specific host grant. Apply the schema with the administrative client, then set the paths and port for that disposable server:

```bash
mysql --protocol=socket --socket=/path/to/mysql.sock -u root resilient_test < app/schema.sql
TEST_MYSQL_SOCKET=/path/to/mysql.sock TEST_MYSQL_PORT=13306 \
  TEST_MYSQL_CA=/path/to/local-ca.pem .venv/bin/python -m unittest discover -s tests -v
```

For the optional Apache end-to-end test, additionally set `TEST_APACHE_URL` to an isolated Apache instance configured from [apache.conf](deploy/apache.conf), backed by the same disposable server. Its Python process must use the disposable runtime credentials through a test-only mocked Secrets Manager client. The test verifies static content, proxy routes, persistence and cleanup.

The database integration tests mock only the Secrets Manager response; they use the real PyMySQL driver, TLS verification, restricted runtime account, schema, SQL writes, and admin readback. They remove their synthetic rows. They must not be pointed at shared or production databases. Actual AWS credentials and RDS behavior still require the integration steps below.

## RDS preparation

Use an administrative identity over verified TLS from a host with private database access. Create a database such as `resilient_app`, then apply [schema.sql](schema.sql) to it. The `CREATE TABLE IF NOT EXISTS` statements can be reapplied to an unchanged schema; they do not migrate existing tables. Do this as a controlled initialization step, not concurrently on each Auto Scaling instance. The application never creates its own tables.

Using an administrative SQL session, create a dedicated runtime account with `REQUIRE SSL`, then apply only these grants (replace the account/host and database with your chosen values). Choose a securely generated password interactively; do not put it in the repository or command line:

```sql
GRANT INSERT ON resilient_app.contact_submissions TO 'resilient_app'@'%';
GRANT SELECT, INSERT, DELETE ON resilient_app.connectivity_probe TO 'resilient_app'@'%';
```

For example, an interactive administrator can use `CREATE USER 'resilient_app'@'%' IDENTIFIED BY '<secure-password>' REQUIRE SSL;` before the grants. Replace the placeholder securely and avoid SQL client history and session logging. A wildcard MySQL account host does not bypass the RDS security group; restrict database network access to the app instances. If the account already exists, inspect and reconcile its grants and TLS requirement rather than assuming the statements removed prior privileges. Consider the RDS `require_secure_transport` parameter as an additional database-wide control. Apply the schema with an administrator client using the RDS endpoint and verified identity, for example `mysql --ssl-mode=VERIFY_IDENTITY --ssl-ca=/path/to/rds-ca-bundle.pem -h <rds-endpoint> -u <admin-user> -p resilient_app < app/schema.sql`.

Keep account creation and password entry out of shell history, logs, Terraform configuration, and state. The runtime account needs no administrative or contact-table read privileges. Create an AWS Secrets Manager secret containing a JSON `SecretString` with nonempty `username` and `password` for this account, and put only its ARN in instance configuration. An administrator account is needed for later readback and cleanup of test submissions. The CLI probe has separate privileges because it verifies and deletes its own row.

## EC2 configuration and installation

These examples target Amazon Linux 2023, systemd, Apache `httpd`, and Python 3.11. They are preparation instructions; no AWS resources are created by this repository. Supply the repository files on the instance through your chosen deployment or image-building process. From a private instance with package access:

```bash
sudo dnf install -y httpd python3.11 python3.11-pip
id -u resilient-app >/dev/null 2>&1 || sudo useradd --system --home-dir /nonexistent --shell /sbin/nologin resilient-app
sudo install -d /opt/resilient-infra
# Run from a checkout staged on the instance; omit local secrets and caches.
tar --exclude=app/.env --exclude='app/.env.*' --exclude='*/__pycache__' -cf - app | sudo tar --no-same-owner -xf - -C /opt/resilient-infra
sudo chown -R root:root /opt/resilient-infra/app
sudo chmod -R go-w /opt/resilient-infra/app
sudo python3.11 -m venv /opt/resilient-infra/.venv
sudo /opt/resilient-infra/.venv/bin/pip install -r /opt/resilient-infra/app/requirements.txt
sudo mkdir -p /etc/resilient-infra
sudo cp /opt/resilient-infra/app/deploy/app.env.example /etc/resilient-infra/app.env
sudo cp /opt/resilient-infra/app/deploy/resilient-infra.service /etc/systemd/system/
sudo cp /opt/resilient-infra/app/deploy/apache.conf /etc/httpd/conf.d/resilient-infra.conf
# Edit /etc/resilient-infra/app.env and place the RDS CA bundle at the configured path.
sudo chmod 644 /etc/resilient-infra/app.env
sudo systemctl daemon-reload
sudo httpd -t
sudo systemctl enable --now resilient-infra httpd
```

Run installation only after the files and non-secret environment are in place; set the Auto Scaling health-check grace period to cover boot and installation. The application starts without a database or secret connection; missing configuration or inaccessible dependencies are visible on write attempts and in the separate diagnostic, not in liveness. For updates on an existing instance, restart the service after replacing the files.

Ensure the copied files, Python environment, CA bundle, and parent directories are readable/traversable by `resilient-app` and Apache; keep application code root-owned and not writable by the service account. The environment file contains only non-secret settings. If another Apache virtual host takes precedence, disable or adapt it. Configure the ALB to reach Apache on port 80; the Python port 8000 must remain loopback-only. The provided Apache file assumes TLS terminates at the ALB. Limit instance port 80 to the ALB security group and database access to the application tier. Adapt the service and paths if using another distribution. Check `getenforce` and Apache's proxy/file labels if using SELinux enforcing mode; AL2023 defaults to permissive, but an enforcing image requires an appropriate policy and labels. The service disables Gunicorn's optional control socket, binds only loopback, and writes operational logs to the journal. Apache access logs omit query strings, referrers, and bodies; configure CloudWatch collection separately.

| Setting | Value |
| --- | --- |
| `AWS_REGION` | Secret's region. |
| `DB_SECRET_ID` | Dedicated runtime secret ARN. |
| `DB_HOST` | Actual RDS DNS endpoint, matching its certificate. |
| `DB_NAME` | Prepared database name. |
| `DB_PORT` | Database port, normally `3306`. |
| `DB_CA_BUNDLE` | Readable RDS CA bundle path. |

Use the appropriate bundle from [Amazon RDS TLS documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html). TLS 1.2+, CA validation, and hostname verification are mandatory; do not substitute an IP address or unverified TLS. The instance role needs `secretsmanager:GetSecretValue` for the runtime secret and, if applicable, `kms:Decrypt` for its key. It also needs DNS and HTTPS connectivity to Secrets Manager via a VPC endpoint or NAT, and connectivity to RDS. Boto3 uses the instance role through its default credential chain; do not configure long-lived AWS keys.

## AWS integration validation

After initialization and instance startup, use Session Manager on an application instance:

```bash
sudo systemctl status resilient-infra httpd --no-pager
curl --fail-with-body http://127.0.0.1/health
curl --fail-with-body -H 'Content-Type: application/json' \
  -d '{"name":"Demo User","email":"demo@example.com","subject":"AWS integration test","message":"Synthetic test only"}' \
  http://127.0.0.1/api/contact
sudo journalctl -u resilient-infra -n 100 --no-pager
```

Verify the exact synthetic row using the separate administrative account over verified TLS, then delete that specific test row if desired. To exercise the retained private probe with the same non-secret settings, run it as the service user (whose instance-role credentials are supplied by IMDS):

```bash
cd /opt/resilient-infra
sudo -u resilient-app bash -c 'set -a; source /etc/resilient-infra/app.env; set +a; .venv/bin/python -m app.backend health'
sudo -u resilient-app bash -c 'set -a; source /etc/resilient-infra/app.env; set +a; .venv/bin/python -m app.backend probe'
```

A successful probe reports its UUID and `cleaned_up: true`. If cleanup cannot be confirmed, inspect and delete only the reported UUID as administrator. Test the website form and `/health` through the ALB's HTTPS address, verify the stored synthetic row, and record results. During a controlled dependency outage, confirm `/health` stays 200, writes return 503, the dependency diagnostic fails, and the ASG does not replace instances solely due to the outage. After restoring access, confirm new writes succeed without restarting Python and prior committed rows remain. For actual RDS failover, check DNS/network recovery and write/read results; in-flight writes can fail and an interrupted commit can have an unknown outcome, so retrying a submission may produce a duplicate. Separately stop the Python service or Apache and confirm the ALB marks that instance unhealthy and the configured ASG policy replaces it. Test across Availability Zones and record the behavior. The local MariaDB test is not evidence of real RDS, IAM, VPC, ALB, or browser behavior.

## Run the smoke test from a terminal

The executable script uses only Python's standard library. From the repository root, run it directly in a terminal; it does not require GitHub Actions:

```bash
# Application running on this machine on port 8000:
./scripts/smoke_test.py http://127.0.0.1:8000

# Later, against the deployed HTTPS website; replace the example domain:
./scripts/smoke_test.py https://YOUR_APP_DOMAIN
```

The HTTPS hostname must match a certificate trusted by your machine. The script checks both endpoints and prints a result and reason for each, for example:

```text
PASS GET /health: HTTP 200
PASS POST /api/contact: HTTP 201
Synthetic submission subject: smoke-test-<unique-id>
PASS smoke test
```

If, for example, the application is running but its database is unavailable, the output can include:

```text
PASS GET /health: HTTP 200
FAIL POST /api/contact: HTTP 503 (service unavailable; check application logs and dependency access)
Synthetic submission subject: smoke-test-<unique-id>
FAIL smoke test
```

It exits `0` only if both checks pass and nonzero otherwise, so the same command works in GitHub Actions. A successful POST means the API acknowledged the write; use the RDS readback below to verify the row independently. Each run attempts to create one synthetic row and prints its unique subject. Even if a POST times out or returns an error, it may have committed; look up that subject before retrying or cleaning up.

### Start the application locally

The local Python server exposes the API on port 8000; it does not serve the website. For a successful contact check, the machine running it must be able to retrieve the configured Secrets Manager secret and reach the prepared MySQL database over verified TLS. A private RDS instance is reachable only from an appropriately connected network or host; starting Python alone does not create a database, secret, or network access. Use the real RDS endpoint as `DB_HOST` so certificate hostname verification works.

From the repository root, after installing the Python dependencies as shown above:

```bash
# Create the local configuration only if it does not already exist.
[ -f app/.env ] || cp app/.env.example app/.env
# Edit app/.env: set AWS_REGION, DB_SECRET_ID, DB_HOST, DB_NAME, DB_PORT and DB_CA_BUNDLE.
# Use the normal AWS credential chain with permission to read the runtime secret.
source app/.env
.venv/bin/gunicorn --no-control-socket --bind 127.0.0.1:8000 --workers 2 --worker-class gthread --threads 4 --timeout 40 app.web:application
```

Leave that terminal running and execute `./scripts/smoke_test.py http://127.0.0.1:8000` in a second terminal from the repository root. Stop Gunicorn with Ctrl+C. The dependency-free health check may pass even when the database or secret is unavailable; the contact check will then fail. Check the Gunicorn output and configuration for the cause. The website form itself is served by Apache in the deployed stack.

### Submit the website form and verify the RDS row

1. Open `https://YOUR_APP_DOMAIN`, scroll to **Send a Message**, and submit synthetic values. For example, use name `Demo User`, email `demo@example.com`, subject `manual-test-20261031-1440`, and message `Synthetic manual RDS test`. Use a distinct subject for each run. Confirm the form says the test submission was saved; no email is sent.
2. From an administrative host with private network access to RDS (for example, an EC2 instance reached through Session Manager), use a MySQL client that supports `--ssl-mode=VERIFY_IDENTITY`. Replace the endpoint, administrator username, CA path, and database name with your values. The client prompts for the administrator password; do not put it in the command or use the insert-only application account.

   ```bash
   mysql --ssl-mode=VERIFY_IDENTITY --ssl-ca=/etc/resilient-infra/rds-ca-bundle.pem \
     --host=YOUR_RDS_ENDPOINT --user=YOUR_ADMIN_USERNAME --password resilient_app
   ```

3. In the MySQL prompt, query the exact subject used in step 1:

   ```sql
   SELECT id, name, email, subject, message, created_at
   FROM contact_submissions
   WHERE subject = 'manual-test-20261031-1440'
   ORDER BY id DESC;
   ```

   Confirm the returned row contains the synthetic values. `created_at` is displayed in the database session's time zone. The same query works with the `smoke-test-...` subject printed by the script. Re-run the query after an instance replacement or RDS failover to check persistence. Type `exit` to leave the MySQL client; remove test rows separately as an administrator if desired.
