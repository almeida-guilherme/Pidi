# Putting Pidi on AWS Lightsail

One small server runs everything with Docker: the app (`Dockerfile`) and Caddy in front of it for HTTPS (`compose.yaml`, `deploy/Caddyfile`). All data (database, recordings, documents, the secret key) is in one Docker volume on the server's disk, which Lightsail snapshots back up.

You need: an AWS account, a domain (or a subdomain of one you have) and your OpenAI key. About 30 minutes.

## 1. Create the server

In the [Lightsail console](https://lightsail.aws.amazon.com/):

1. **Create instance**. Region: the one closest to you (e.g. São Paulo, `sa-east-1`). Platform **Linux/Unix**, blueprint **OS Only → Ubuntu 24.04 LTS**.
2. Plan: **2 GB RAM** (enough for one user and long recordings). Name it `pidi`. Create.
3. **Networking** tab of the instance:
   - **Create static IP** and attach it to `pidi` (otherwise the IP changes when the server restarts).
   - **IPv4 Firewall**: add a rule for **HTTPS (443)**. HTTP (80) and SSH (22) are there already; Caddy needs 80 to get the certificate. Optionally limit SSH to your own IP.

## 2. Point the domain at it

At your domain's DNS (Lightsail **Domains & DNS**, Route 53 or your registrar), add an **A record**: `pidi.yourdomain.com` → the static IP. It can take a few minutes to work; `ping pidi.yourdomain.com` should show the static IP.

## 3. Install Docker

Open the server's terminal (**Connect using SSH** in the console, or `ssh ubuntu@<static IP>` with the key Lightsail gives you), then:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu        # use docker without sudo
exit                                  # log out and connect again for that to apply
```

A swap file avoids running out of memory while the image is built:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 4. Get the app and configure it

```bash
git clone https://github.com/almeida-guilherme/Pidi.git pidi
cd pidi
cp deploy/env.example .env
nano .env        # set PDD_DOMAIN, OPENAI_API_KEY, the Google client (below), PDD_OPERATOR_NAME, PDD_CONTACT_EMAIL (and email, step 7); save with Ctrl+O, Enter, Ctrl+X
chmod 600 .env
```

People log in with Google, so the site needs a Google OAuth client. In [Google Cloud Console](https://console.cloud.google.com/), in any project: *APIs & Services → OAuth consent screen* (user type External, app name Pidi, your email; while it is in *Testing*, add the Google accounts that may log in as test users), then *Credentials → Create credentials → OAuth client ID*, type *Web application*, authorized redirect URI `https://pidi.yourdomain.com/api/auth/google/callback`. Put the client ID and secret in `PDD_GOOGLE_CLIENT_ID` and `PDD_GOOGLE_CLIENT_SECRET`.

## 5. Start it

```bash
docker compose up -d --build     # the first build takes a few minutes
docker compose logs -f caddy     # wait for "certificate obtained successfully", then Ctrl+C
```

Add your email (sign-up is closed in `.env`, so only emails added this way can log in), then log in with that Google account:

```bash
docker compose exec app python -m web.create_user you@example.com
```

Open `https://pidi.yourdomain.com` and log in. New accounts get `PDD_SIGNUP_CREDITS` (default 10) credits; a project costs one per started minute of recording. Give more with:

```bash
docker compose exec app python -m web.grant_credits you@example.com 100 "October"
```

## 6. Backups

Instance → **Snapshots** → turn on **Automatic snapshots** (daily, the last 7 kept). A snapshot holds the whole disk, including the data volume. To restore, create a new instance from a snapshot and attach the static IP to it.

## 7. Email and alerts

Email is how failed projects and server errors reach you (`PDD_ALERT_EMAIL`). Without it the app still runs; emails are only written to `docker compose logs app`. Two simple options:

- **Amazon SES** (cheap, same AWS account): in the SES console, in your region, *Create identity* for your domain (add the DNS records it shows) or just your email address, then *SMTP settings → Create SMTP credentials*. Put the endpoint in `PDD_SMTP_HOST` (e.g. `email-smtp.sa-east-1.amazonaws.com`), the credentials in `PDD_SMTP_USER`/`PDD_SMTP_PASSWORD`, and an address of the verified identity in `PDD_MAIL_FROM`. New SES accounts can only send to verified addresses until you ask AWS for production access, which is fine while you're the only user.
- **Gmail**: turn on 2-step verification, create an *App password* (Google account → Security), and set `PDD_SMTP_HOST=smtp.gmail.com`, `PDD_SMTP_USER` to your Gmail address, `PDD_SMTP_PASSWORD` to the app password, `PDD_MAIL_FROM` to the same address.

After changing `.env`: `docker compose up -d`.

## 8. Uptime monitor

`https://pidi.yourdomain.com/api/health` answers 200 while the app and its workers run, and 503 when a worker stopped. A free monitor (e.g. UptimeRobot) checking it every few minutes emails you if the site goes down.

## 9. Selling credits (Asaas)

People buy credits on Asaas's checkout page (Pix or card); the site adds them when Asaas's webhook says the payment was confirmed. Start in Asaas's sandbox (https://sandbox.asaas.com, no real money), then repeat with the production account.

1. In Asaas: **Integrações → Chaves de API → Gerar chave**. Put it in `.env` as `PDD_ASAAS_API_KEY` (in single quotes, since it starts with `$`), with `PDD_ASAAS_SANDBOX=true` for a sandbox key or `false` for a production one.
2. Make a webhook token: `openssl rand -hex 32`. Put it in `.env` as `PDD_ASAAS_WEBHOOK_TOKEN`.
3. In Asaas: **Integrações → Webhooks → Adicionar**: URL `https://pidi.yourdomain.com/api/payments/webhook`, the same token in *Token de autenticação*, API version v3, and the events **CHECKOUT_PAID**, **CHECKOUT_CANCELED** and **CHECKOUT_EXPIRED**. Keep it enabled.
4. Set the price and limits if the defaults don't suit (`PDD_CREDIT_PRICE_CENTS=150`, `PDD_CREDIT_MIN=10`, `PDD_CREDIT_MAX=1000`, and the fixed packs `PDD_CREDIT_PACKS=10,30`), then `docker compose up -d`.
5. Buy the fewest credits allowed on the site's Credits page and check the credits arrive. If they don't, look at `docker compose logs app | grep -i asaas` and at the webhook's log in Asaas (a 401 means the tokens differ).

Payments that are refunded or charged back in Asaas don't take the credits away by themselves: take them back with `grant_credits` and a negative number.

## Everyday tasks

| Task | Command (in `~/pidi` on the server) |
|---|---|
| Update to the latest version | `git pull && docker compose up -d --build` |
| See what the app is doing | `docker compose logs -f app` |
| Let someone in (sign-up closed) | `docker compose exec app python -m web.create_user them@example.com` |
| Give someone credits (negative takes them back) | `docker compose exec app python -m web.grant_credits them@example.com 30 "note"` |
| See someone's credits | `docker compose exec app python -m web.grant_credits them@example.com` |
| Restart | `docker compose restart` |
| Disk space used by recordings | `docker system df -v \| grep pidi-data` |

## Costs and limits

- The 2 GB Lightsail plan is a fixed monthly price (check the current price and disk size in the console). A 40-minute project takes about 550 MB of disk (the recording, about 250 MB, plus its screenshots and audio), so a 60 GB disk holds roughly 90 of them at once. Once a recording is deleted (retention, below) a project keeps only its document and screenshots, a few MB; deleting a project frees all of it.
- OpenAI usage is separate, and every project runs on your key: about $0.40 for a 5-minute recording, about $2 for 40 minutes. Use a key of its own OpenAI project and set a monthly budget limit for it in the OpenAI dashboard: if the server is ever broken into, that limit is the most the key can spend. People pay with credits; a project that costs more than `PDD_MAX_COST_PER_CREDIT_USD` (default $0.15) per credit is stopped and its credits are given back.
- Two projects are processed at the same time, one per person (`PDD_WORKERS`, `PDD_RUNS_PER_USER`); on the 2 GB plan, don't raise the workers much further.
- Recordings are deleted 30 days after their document (`PDD_RETENTION_DAYS`), which keeps the disk from filling up; documents stay.
