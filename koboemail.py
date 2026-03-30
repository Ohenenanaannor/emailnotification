import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os
import time
from dotenv import load_dotenv
import psycopg2
from flask import Flask
import threading
import traceback

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

    print("🔍 FULL SUBMISSION:", sub)  # DEBUG

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

    print("🚨 Serious:", serious)
    print("⚠️ Moderate:", moderate)
    print("ℹ️ Info:", info)

    return serious, moderate, info

# ------------------------
# EMAIL
# ------------------------
def send_email(subject, body):
    try:
        print(f"📧 Preparing to send email: {subject}")

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = SUPERVISOR_EMAIL
        msg.attach(MIMEText(body, "html"))

        print(f"📧 Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}...")

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
            server.set_debuglevel(1)  # Show SMTP protocol in logs
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            print("📧 Logged in successfully")
            server.send_message(msg)
            print(f"📧 Email sent to {SUPERVISOR_EMAIL} ✅")

    except smtplib.SMTPAuthenticationError:
        print("❌ SMTP AUTHENTICATION FAILED. Check EMAIL_USER and EMAIL_PASSWORD")
        traceback.print_exc()
    except smtplib.SMTPConnectError:
        print("❌ SMTP CONNECTION FAILED. Could be blocked by Render or wrong server/port")
        traceback.print_exc()
    except Exception as e:
        print("❌ EMAIL ERROR:", e)
        traceback.print_exc()

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
# FETCH KOBO
# ------------------------
def fetch():
    try:
        print("🌐 Calling Kobo API...")

        r = requests.get(
            KOBOTOOLBOX_API_URL,
            auth=(KOBOTOOLBOX_USERNAME, KOBOTOOLBOX_PASSWORD)
        )

        print("🌐 STATUS:", r.status_code)
        print("🌐 RAW RESPONSE:", r.text[:300])

        if r.status_code != 200:
            return []

        data = r.json()
        results = data.get("results", [])

        print(f"📦 FETCHED {len(results)} submissions")

        return results

    except Exception as e:
        print("❌ FETCH ERROR:", e)
        traceback.print_exc()
        return []

# ------------------------
# MAIN
# ------------------------
def main():
    print("🔁 RUNNING MAIN")

    create_table()
    subs = sorted(fetch(), key=lambda x: x.get("_id", 0))

    for sub in subs:
        sub_id = sub.get("_id")

        print(f"➡️ Processing {sub_id}")

        if not sub_id:
            continue

        if is_processed(sub_id):
            print("⏭️ Already processed")
            continue

        serious, moderate, info = categorize_issues(sub)

        if serious or moderate or info:
            subject = f"Vehicle Report - {find_value(sub, 'vehicle')}"
            body = format_email(sub, serious, moderate, info)

            send_email(subject, body)
        else:
            print("ℹ️ No issues found")

        mark_processed(sub_id)

# ------------------------
# WORKER LOOP
# ------------------------
def run_worker():
    print("🚀 WORKER STARTED")

    while True:
        try:
            main()
        except Exception as e:
            print("❌ LOOP ERROR:", e)
            traceback.print_exc()

        time.sleep(300)

# ------------------------
# FLASK
# ------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Kobo Email Service Running ✅"

# ------------------------
# START
# ------------------------
if __name__ == "__main__":
    threading.Thread(target=run_worker, daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))  # Use Render’s port or default 10000 locally
    app.run(host="0.0.0.0", port=port)