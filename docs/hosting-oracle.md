# Hosting the public demo for free (Oracle Cloud + Gemini)

This puts the **fake demo shop** online at a public HTTPS link that anyone can try. It costs **$0**:

- **Server:** an Oracle Cloud *Always Free* ARM machine (up to 4 cores and 24 GB RAM, free forever).
- **AI:** Google Gemini's free tier. The demo data is made up, so the free tier's data terms don't matter.
- **HTTPS:** Caddy fetches a Let's Encrypt certificate automatically. With a free `sslip.io` name you don't
  need to buy a domain.

Visitors can use **Try a question** and see a **read-only** Connectors page. Admin pages stay behind your password.
To protect the free AI quota:
- each visitor gets 5 questions per 10 minutes;
- the whole site gets 150 questions a day;
- suggested questions are answered once and then served instantly.

> Never host your company's real setup this way. This guide is only for the public demo shop.

---

## 1. Get a free Gemini API key (2 minutes)
1. Open **https://aistudio.google.com** and sign in with a Google account.
2. Click **Get API key → Create API key** and copy it. No card is needed.

## 2. Create the free server (15 minutes)
1. Sign up at **https://www.oracle.com/cloud/free/**. A card is needed to verify you; *Always Free* resources are
   never charged.
2. Go to **Compute → Instances → Create instance**:
   - **Image:** Ubuntu 24.04 (the aarch64 build is chosen automatically for Ampere).
   - **Shape:** Ampere → `VM.Standard.A1.Flex`, 2 OCPU / 12 GB. That's plenty, and it's within the free limits.
   - **Networking:** keep the default VCN and public subnet, and *assign a public IPv4 address*.
   - **SSH keys:** upload your public key (e.g. `~/.ssh/id_ed25519.pub`).
   - If you see **"Out of capacity"**, pick another *availability domain*, try a smaller shape (1 OCPU / 6 GB),
     or try again later.
3. Note the instance's **public IP**, e.g. `140.238.10.20`.

## 3. Open ports 80 and 443 (two places!)
**a) In the Oracle console:** Instance → *Subnet* → *Default Security List* → **Add Ingress Rules**:
source `0.0.0.0/0`, TCP, destination ports `80,443`.

**b) On the server itself.** Oracle's Ubuntu images block these ports with their own firewall:
```bash
ssh ubuntu@<public-ip>
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 4. Install Docker
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && newgrp docker
```

## 5. Get the code
The repository is private, so give the server **read-only** access with a deploy key:
```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/askcite_deploy
cat ~/.ssh/askcite_deploy.pub
```
On GitHub, go to the repository → *Settings → Deploy keys → Add deploy key*, paste the key, and leave *write
access* off. Then:
```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/askcite_deploy" git clone git@github.com:shadabshaikh0/askcite.git
```
If the repository is public, a plain `git clone https://github.com/shadabshaikh0/askcite.git` works.

## 6. Configure and start
```bash
cd askcite/deploy/demo
cp .env.example .env
nano .env
```
Set these values:
- `GEMINI_API_KEY`: your key from step 1.
- `DEMO_HOST`: `askcite.<public-ip>.sslip.io`, e.g. `askcite.140.238.10.20.sslip.io`.
- `ASKCITE_ADMIN_PASSWORD`: a long password, for you only.
- `POSTGRES_PASSWORD`: anything long.

Then start everything:
```bash
docker compose up -d --build
docker compose logs -f app
```
The first start builds the image, creates the fake shop, syncs it, and pre-answers the suggested questions in
the background. That takes a few minutes. Then open **https://askcite.&lt;public-ip&gt;.sslip.io**.

## 7. Everyday use
| Task | Command (in `deploy/demo`) |
|---|---|
| See logs | `docker compose logs -f app` |
| Update to the latest code | `git pull && docker compose up -d --build` |
| Restart | `docker compose restart app` |
| Stop | `docker compose down` (add `-v` to also delete the demo data) |
| Admin pages | open `https://<DEMO_HOST>/login` (user `admin`) |

Containers restart by themselves after a server reboot.

## If something goes wrong
| You see | Fix |
|---|---|
| Answers say *"something went wrong"* | Run `docker compose logs app \| grep failed`. `API key not valid` means: fix `GEMINI_API_KEY` in `.env`, then run `docker compose up -d`. |
| Answers are slow or *"busy"* | The free Gemini tier allows about 10 requests a minute. Askcite retries, so wait a minute. |
| The HTTPS page doesn't load | Check that ports 80/443 are open in **both** places (step 3), and that `DEMO_HOST` contains the server's current public IP. |
| The app can't connect to its database after you changed `POSTGRES_PASSWORD` | Postgres keeps the password it was first started with. Run `docker compose down -v && docker compose up -d` (this rebuilds the demo data). |

## Try it on your laptop first
```bash
cd deploy/demo
cp .env.example .env    # keep DEMO_HOST=http://localhost, add your GEMINI_API_KEY
docker compose up --build
```
Then open **http://localhost**.

## Limits and settings
In `examples/demo-shop/config/sources.yaml`, under `demo:`, you can set the suggested questions, the banner,
the per-visitor and daily limits, and how long answers are cached.

If the free Gemini quota feels tight, try `ASKCITE_MODEL=gemini/gemini-flash-lite-latest`, which has higher free
limits, or set a paid model such as `anthropic/claude-sonnet-5-5` together with its API key.
