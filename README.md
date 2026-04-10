# SQL Account Print

A web app that lets users fetch and print SQL Account documents (Invoice, DO, Quotation, etc.) as PDFs via the SQL Account REST API.

Built with [NiceGUI](https://nicegui.io/) (Python).

---

## Features

- Print 7 document types: Sales Quotation, Sales Order, Delivery Order, Sales Invoice, Cash Sale, Credit Note, Debit Note
- Multiple company databases — each with its own API credentials
- Per-user login with company access control
- Report format selection (FR3 / RTM) — upload Report Designer Excel to auto-populate
- Admin panel for managing companies, users, and templates
- API keys encrypted at rest
- Login rate limiting and audit logging

---

## Server Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 22.04 / Debian 12 (64-bit) | Ubuntu 24.04 LTS |
| CPU | 1 vCPU | 2 vCPU |
| RAM | 1 GB | 2 GB |
| Disk | 10 GB SSD | 20 GB SSD |
| Python | 3.11 or 3.12 | 3.12 |
| Network | Static public IP, ports 22/80/443 | |

> **Note:** Do NOT use Python 3.14 — some dependencies have compatibility issues.

---

## Deployment (Step-by-Step)

### 1. Server Initial Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx ufw git

# Create app user
sudo useradd -r -m -s /bin/bash sqlprint

# Enable firewall
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### 2. Clone the Repository

```bash
sudo su - sqlprint

git clone https://github.com/REPO_OWNER/sql-account-print-via-api.git /opt/sqlprintapp
cd /opt/sqlprintapp

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env

# Generate a secure session secret
python3 -c "import secrets; print(secrets.token_hex(32))"

# Edit .env — paste the generated secret as SESSION_SECRET
nano .env

# Lock down permissions
chmod 600 .env
```

**`.env` file contents:**

```
SQLACC_AWS_REGION=ap-southeast-1
SESSION_SECRET=<paste-64-char-hex-here>
LOG_DIR=./logs
DOC_TYPES_FILE=./doc_types.json
COMPANIES_FILE=./companies.json
USERS_FILE=./users.json
DEFAULT_TEMPLATES_FILE=./default_templates.json
```

### 4. Create Admin Account

```bash
source .venv/bin/activate
python manage.py create-admin
# Enter username, password (min 8 chars), confirm
```

### 5. Quick Test

```bash
python main.py
# Should print: "NiceGUI ready to go on http://..."
# Ctrl+C to stop
```

### 6. Production Settings

Edit `main.py` — change these lines for production:

```python
# Change:
port=8090,
show=True,

# To:
port=8090,
show=False,
host='127.0.0.1',
```

### 7. Set Up systemd Service

```bash
exit  # back to root/sudo user

sudo nano /etc/systemd/system/sqlprintapp.service
```

Paste:

```ini
[Unit]
Description=SQL Account Print Web App
After=network.target

[Service]
Type=simple
User=sqlprint
Group=sqlprint
WorkingDirectory=/opt/sqlprintapp
ExecStart=/opt/sqlprintapp/.venv/bin/python main.py
Restart=always
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-runner.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable sqlprintapp
sudo systemctl start sqlprintapp
sudo systemctl status sqlprintapp
```

### 8. Set Up nginx Reverse Proxy

```bash
sudo nano /etc/nginx/sites-available/sqlprintapp
```

Paste (replace `your-domain.com`):

```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (required by NiceGUI)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    client_max_body_size 10M;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/sqlprintapp /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### 9. Set Up HTTPS (Free SSL)

```bash
sudo certbot --nginx -d your-domain.com
# Follow the prompts — certbot auto-configures nginx + auto-renewal
```

### 10. Verify

Open `https://your-domain.com` in a browser — you should see the login page.

---

## Information Needed Before Deployment

| Item | Who Provides | Example |
|---|---|---|
| GitHub repo URL | App admin | `https://github.com/...` |
| Domain name | App admin | `print.yourcompany.com` |
| DNS A record | App admin / Domain registrar | Point domain → VPS IP |
| VPS public IP | Server guy | `203.0.113.50` |

**After deployment**, the app admin will configure everything via the browser (Admin panel):
- Add companies (API host, Access Key, Secret Key)
- Create user accounts
- Upload report templates per company

---

## Firewall Note

The VPS must be able to reach each client's SQL Account API service on **port 443** (HTTPS).

Ask the client's IT team to **whitelist the VPS public IP** in their firewall for inbound port 443.

---

## Maintenance

```bash
# View app status
sudo systemctl status sqlprintapp

# View live logs
sudo journalctl -u sqlprintapp -f

# View print audit logs
tail -f /opt/sqlprintapp/logs/print_*.log

# Update code (after git push)
sudo su - sqlprint -c "cd /opt/sqlprintapp && git pull"
sudo systemctl restart sqlprintapp

# Renew SSL (auto, but manual if needed)
sudo certbot renew
```

---

## Architecture

```
User Browser → HTTPS (nginx) → NiceGUI app (port 8090)
                                     ↓
                          SQL Account REST API
                          (customer's server, port 443)
                                     ↓
                              PDF returned to browser
```

## Security

- API keys encrypted at rest (Fernet, derived from SESSION_SECRET)
- API keys never shown to non-admin users
- Login rate-limited (5 attempts/minute)
- Failed logins audited in log files
- Sessions use signed cookies (itsdangerous + SameSite=Strict)
- HTTPS enforced via nginx + Let's Encrypt
- Passwords hashed with bcrypt (min 8 characters)
