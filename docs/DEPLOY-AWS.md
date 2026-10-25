# Deploying Ease as a public website on AWS (with your AWS credits)

The result is a public HTTPS website such as `https://13-234-5-6.sslip.io`. Anyone can sign up, add their own free
AI keys and use every feature. Accounts and saved keys survive restarts, and your laptop can be off.

**Cost:** about $35–40 a month, paid from your credits:
- a t3.medium server (2 vCPU, 4 GB) ≈ $30
- a 30 GB disk ≈ $2.50
- a public IPv4 address ≈ $3.60

Before you start, check **Billing → Credits** to see which services your credits cover (EC2 is normally included).

## 1. Launch the server (10 minutes)

1. Sign in at https://console.aws.amazon.com. In the top-right region menu, pick **Asia Pacific (Mumbai)
   ap-south-1** (closest to India).
2. Search for **EC2** and open it. Click **Launch instance**.
3. **Name:** `ease`
4. **Application and OS Images:** **Ubuntu**, **Ubuntu Server 24.04 LTS** (64-bit x86).
5. **Instance type:** **t3.medium**. Choose t3.large (8 GB, about double the cost) if your credits allow; it's faster.
6. **Key pair:** **Proceed without a key pair**. You won't need to log in; the browser-based connect works without one.
7. **Network settings → Edit:**
   - Keep "Create security group".
   - **Allow HTTPS traffic from the internet:** ✅
   - **Allow HTTP traffic from the internet:** ✅ (needed once to get the certificate)
   - **Allow SSH traffic:** leave it ticked, source **Anywhere**. It's only used by EC2 Instance Connect in your
     browser, and there are no passwords to guess.
8. **Configure storage:** change 8 to **30** GiB (gp3).
9. **Advanced details** (expand it), scroll to the bottom, **User data:** open
   `deploy/aws/user-data.sh` from this repository, copy **all** of it, and paste it into the box.
10. Click **Launch instance**.

## 2. Wait for the setup (15–20 minutes)

The server installs Docker, downloads Ease, builds it and starts it by itself.

- **Your website address:** open the instance in EC2 and copy its **Public IPv4 address**, e.g. `13.234.5.6`.
  Replace the dots with dashes and add `.sslip.io`: **`https://13-234-5-6.sslip.io`**
- Open it after about 15–20 minutes. Until then the page won't load, which is normal.
- **To watch progress** (optional): select the instance, then **Connect → EC2 Instance Connect → Connect**, and run:
  ```bash
  sudo tail -f /var/log/ease-setup.log
  ```
  It's finished when the output ends with `Ease is at https://…`. The address is also saved in
  `/opt/ease/URL.txt`.

## 3. Use it and share it

Open the address, choose **Create an account**, then add your Groq and Gemini keys under
**Profile & apps → AI model keys**. Share the link. Everyone signs up and uses their own free keys, as the in-app
**Getting started** guide explains.

## Keep the same address (recommended)

The public IP changes if you **Stop** and **Start** the instance (a Reboot keeps it). To make it permanent:

1. Go to **EC2 → Elastic IPs → Allocate Elastic IP address → Allocate**.
2. Choose **Actions → Associate Elastic IP address**, pick the `ease` instance, then **Associate**.
3. **Reboot** the instance once (Instance state → Reboot).

The site moves to the new address automatically on boot: the new IP with dashes, `.sslip.io`.

## Updating to the latest code

Connect with EC2 Instance Connect and run:

```bash
cd /opt/ease/deploy/aws && sudo docker compose build --no-cache ease && sudo docker compose up -d
```

## Sign-up human check (recommended before sharing the link)

Stops bots from creating accounts in bulk. Free, no card:

1. In the Cloudflare dashboard open **Turnstile → Add widget**, add your site's address (e.g. `3-110-20-5.sslip.io`)
   as the hostname, mode **Managed**.
2. On the server, add the two keys it shows to `/opt/ease/deploy/aws/.env`:
   ```
   TURNSTILE_SITE_KEY=0x4AAAA...
   TURNSTILE_SECRET_KEY=0x4AAAA...
   ```
3. `cd /opt/ease/deploy/aws && sudo docker compose up -d`

The sign-up page then shows the check, and the API refuses sign-ups without a valid one.

## Backups and restore

Every night at 03:30 the database and uploaded resumes are saved to `/opt/ease-backups` (7 days kept). Run one now
with `sudo /opt/ease/deploy/aws/backup.sh`. To restore a database backup:

```bash
cd /opt/ease/deploy/aws
sudo docker compose exec -T ease sh -c 'PGPASSWORD="$(cat /home/user/data/pg.pw)" /usr/lib/postgresql/16/bin/pg_restore -h 127.0.0.1 -U ease -d ease --clean --if-exists' < /opt/ease-backups/db-YYYYMMDD-HHMM.dump
```

Saved API keys in a backup can only be decrypted with the `VAULT_MASTER_KEY` in `deploy/aws/.env`. If you copy
backups off the server, keep that file somewhere else.

## Stopping the costs

**Instance state → Terminate** deletes the server and its data. Also release the Elastic IP if you made one,
because unattached Elastic IPs are billed.

## Security

- Only ports 80 and 443 serve the site; everything else stays inside the server.
- Visitors' API keys are encrypted with a master key generated on the server at setup, and it never leaves it.
- Every visitor runs on their own keys (`USER_KEYS_ONLY=true`), so nobody can spend your quota.
- Rate limits use each visitor's real IP.
- The browser agent cannot reach the internal API, database or Redis.
- The interactive API docs are off.
- The agent never opens banking, payment, crypto or government-identity sites (add more with `BLOCKED_SITES`).
- Website logins a visitor hands over are deleted when their run ends (`REMEMBER_SITE_LOGINS=false`).
- Visitors can delete their account and all its data themselves (Profile & apps → Delete account).
