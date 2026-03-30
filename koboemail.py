import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os
import time
from dotenv import load_dotenv
import psycopg2
from flask import Flask, request
import threading

# ------------------------
# LOAD ENV
# ------------------------
load_dotenv()

KOBOTOOLBOX_API_URL = "https://kf.kobotoolbox.org/api/v2/assets/ahCr8ALo67qrBdaRjQkm8K/data/"
KOBOTOOLBOX_USERNAME = "annorpoku"
KOBOTOOLBOX_PASSWORD = os.getenv("KOBOTOOLBOX_PASSWORD")

SMTP_SERVER = "smtp.infomaniak.com"
SMTP_PORT = 465
EMAIL_USER = "ohenenana.annor@raincoatroofingsystems.com"
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SUPERVISOR_EMAIL = "ohenenanaannor2000@gmail.com"

DATABASE_URL = os.getenv("DATABASE_URL")

# ------------------------
# DATABASE
# ------------------------
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def create_table():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_submissions (
            submission_id BIGINT PRIMARY KEY
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def is_processed(sub_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM processed_submissions WHERE submission_id = %s", (sub_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result is not None

def mark_processed(sub_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO processed_submissions(submission_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (sub_id,)
    )
    conn.commit()
    cur.close()
    conn.close()

# ------------------------
# HELPERS
# ------------------------
def find_value(submission, keyword):
    for key, value in submission.items():
        if keyword.lower() in key.lower():
            return value
    return None

def is_not_ok(value):
    if value is None:
        return False
    value = str(value).lower().strip()
    return value in ["not_okay", "no", "false", "bad", "0"]

def is_yes(value):
    if value is None:
        return False
    value = str(value).lower().strip()
    return value in ["yes", "true", "1"]

# ------------------------
# ISSUE CATEGORIZATION
# ------------------------
def categorize_issues(sub):
    serious, moderate, info = [], [], []

    if is_not_ok(find_value(sub, "engine_oil")):
        serious.append("Engine oil level")
    if is_not_ok(find_value(sub, "coolant")):
        serious.append("Coolant level")
    if is_not_ok(find_value(sub, "brake")):
        serious.append("Brake fluid")
    if is_not_ok(find_value(sub, "steering")):
        serious.append("Power steering")
    if is_not_ok(find_value(sub, "tyre")):
        serious.append("Tyre condition")
    if is_yes(find_value(sub, "dvla")):
        serious.append("DVLA expired")
    if is_yes(find_value(sub, "road")):
        serious.append("Road worthy expired")
    if is_not_ok(find_value(sub, "horn")):
        moderate.append("Horn not working")
    if is_not_ok(find_value(sub, "clean")):
        info.append("Cleanliness issue")

    return serious, moderate, info

# ------------------------
# EMAIL
# ------------------------
def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = SUPERVISOR_EMAIL
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
        print("📧 EMAIL SENT ✅")
    except Exception as e:
        print("❌ EMAIL ERROR:", e)

# ------------------------
# FORMAT EMAIL
# ------------------------
def format_email(sub, serious, moderate, info):
    vehicle = find_value(sub, "vehicle") or "Unknown"
    driver = find_value(sub, "name") or "Unknown"

    def make_list(items):
        return "".join([f"<li>{i}</li>" for i in items]) or "<li>None</li>"

    return f"""
    <html>
    <body>
        <h3>Vehicle Report</h3>
        <p><b>Vehicle:</b> {vehicle}</p>
        <p><b>Driver:</b> {driver}</p>

        <h4 style="color:red;">Serious</h4>
        <ul>{make_list(serious)}</ul>

        <h4 style="color:orange;">Moderate</h4>
        <ul>{make_list(moderate)}</ul>

        <h4 style="color:green;">Info</h4>
        <ul>{make_list(info)}</ul>
    </body>
    </html>
    """

# ------------------------
# PROCESS SUBMISSION
# ------------------------
def process_submission(sub):
    sub_id = sub.get("_id")
    if not sub_id or is_processed(sub_id):
        return
    serious, moderate, info = categorize_issues(sub)
    if serious or moderate or info:
        subject = f"Vehicle Report - {find_value(sub, 'vehicle')}"
        body = format_email(sub, serious, moderate, info)
        send_email(subject, body)
    mark_processed(sub_id)

# ------------------------
# FETCH KOBO (Backup Fetch)
# ------------------------
def fetch():
    try:
        r = requests.get(
            KOBOTOOLBOX_API_URL,
            auth=(KOBOTOOLBOX_USERNAME, KOBOTOOLBOX_PASSWORD)
        )
        if r.status_code != 200:
            print("❌ Fetch failed:", r.status_code)
            return []
        data = r.json()
        return data.get("results", [])
    except Exception as e:
        print("❌ FETCH ERROR:", e)
        return []

def run_backup_worker():
    while True:
        print("🔁 Running backup fetch...")
        subs = sorted(fetch(), key=lambda x: x.get("_id", 0))
        for sub in subs:
            process_submission(sub)
        time.sleep(600)  # every 10 min

# ------------------------
# FLASK APP (Webhook Receiver)
# ------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Kobo Email Service Running ✅"

@app.route("/kobo-webhook", methods=["POST"])
def kobo_webhook():
    data = request.json
    if data:
        print("🔔 Webhook received:", data.get("_id"))
        process_submission(data)
    else:
        print("❌ Webhook received empty payload")
    return {"status": "success"}, 200

# ------------------------
# START APP
# ------------------------
if __name__ == "__main__":
    create_table()
    threading.Thread(target=run_backup_worker, daemon=True).start()

    # Use Render's port
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)