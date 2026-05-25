import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import time
from dotenv import load_dotenv
import psycopg2
from flask import Flask, request, jsonify
import threading

# ------------------------
# LOAD ENV
# ------------------------
load_dotenv()

KOBOTOOLBOX_API_URL = os.getenv("KOBOTOOLBOX_API_URL")
KOBOTOOLBOX_USERNAME = os.getenv("KOBOTOOLBOX_USERNAME")
KOBOTOOLBOX_PASSWORD = os.getenv("KOBOTOOLBOX_PASSWORD")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

# Comma-separated list of supervisor emails set in Render env vars
# e.g. SUPERVISOR_EMAILS=person1@gmail.com,person2@gmail.com
SUPERVISOR_EMAILS = [
    email.strip()
    for email in os.getenv("SUPERVISOR_EMAILS", "").split(",")
    if email.strip()
]

DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# ------------------------
# STARTUP CHECK
# ------------------------
if not SUPERVISOR_EMAILS:
    print("⚠️ WARNING: SUPERVISOR_EMAILS env variable is not set — no emails will be sent!")
else:
    print(f"📋 Supervisor emails loaded: {SUPERVISOR_EMAILS}")

# ------------------------
# DATABASE
# ------------------------
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def create_table():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_submissions (
                    submission_id BIGINT PRIMARY KEY
                )
            """)

def get_all_processed_ids():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT submission_id FROM processed_submissions")
            rows = cur.fetchall()
            return set(row[0] for row in rows)

def is_already_processed(sub_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM processed_submissions WHERE submission_id = %s", (sub_id,))
            return cur.fetchone() is not None

def mark_processed_bulk(ids):
    if not ids:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO processed_submissions(submission_id) VALUES (%s) ON CONFLICT DO NOTHING",
                [(i,) for i in ids]
            )

def mark_processed_one(sub_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO processed_submissions(submission_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (sub_id,)
            )

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
    return str(value).lower().strip() in ["not_okay", "no", "false", "bad", "0"]

def is_yes(value):
    if value is None:
        return False
    return str(value).lower().strip() in ["yes", "true", "1"]

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
        <h3>Vehicle Inspection Report</h3>
        <p><b>Vehicle:</b> {vehicle}</p>
        <p><b>Driver:</b> {driver}</p>

        <h4 style="color:red;">🔴 Serious Issues</h4>
        <ul>{make_list(serious)}</ul>

        <h4 style="color:orange;">🟠 Moderate Issues</h4>
        <ul>{make_list(moderate)}</ul>

        <h4 style="color:green;">🟢 Info</h4>
        <ul>{make_list(info)}</ul>
    </body>
    </html>
    """

# ------------------------
# BUILD EMAIL MESSAGE
# ------------------------
def build_message(subject, body):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = ", ".join(SUPERVISOR_EMAILS)
    msg.attach(MIMEText(body, "html"))
    return msg

# ------------------------
# SEND SINGLE EMAIL — used by webhook
# Returns True if sent, False if failed
# ------------------------
def send_email(subject, body):
    if not SUPERVISOR_EMAILS:
        print("⚠️ No supervisor emails configured — skipping send")
        return False

    msg = build_message(subject, body)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, SUPERVISOR_EMAILS, msg.as_string())
        print(f"📧 EMAIL SENT to {SUPERVISOR_EMAILS} ✅")
        return True

    except Exception as e:
        print(f"❌ EMAIL ERROR (attempt 1): {e}")

    print("⏳ Waiting 15s before retry...")
    time.sleep(15)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, SUPERVISOR_EMAILS, msg.as_string())
        print(f"📧 EMAIL SENT ON RETRY to {SUPERVISOR_EMAILS} ✅")
        return True

    except Exception as e:
        print(f"❌ EMAIL RETRY FAILED: {e}")
        return False

# ------------------------
# SEND BULK EMAILS — used by polling loop
# Logs in ONCE and sends all in one session
# Returns set of sub_ids successfully sent
# ------------------------
def send_emails_bulk(email_queue):
    if not email_queue:
        return set()

    if not SUPERVISOR_EMAILS:
        print("⚠️ No supervisor emails configured — skipping bulk send")
        return set()

    sent_ids = set()

    try:
        print(f"📬 Opening Gmail SMTP session to send {len(email_queue)} email(s) to {SUPERVISOR_EMAILS}...")
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            print("🔑 Gmail SMTP login successful")

            for sub_id, subject, body in email_queue:
                try:
                    msg = build_message(subject, body)
                    server.sendmail(EMAIL_USER, SUPERVISOR_EMAILS, msg.as_string())
                    print(f"📧 Email sent for submission {sub_id} ✅")
                    sent_ids.add(sub_id)
                    time.sleep(2)

                except Exception as e:
                    print(f"❌ Failed to send email for {sub_id}: {e}")

    except Exception as e:
        print(f"❌ SMTP SESSION ERROR: {e}")
        print("⚠️ No emails sent this cycle — will retry next poll")

    return sent_ids

# ------------------------
# PROCESS ONE SUBMISSION — used by webhook
# ------------------------
def process_submission(sub):
    sub_id = sub.get("_id")

    if not sub_id:
        print("⚠️ Submission missing _id, skipping")
        return

    if is_already_processed(sub_id):
        print(f"⏭️ Already processed {sub_id}, skipping")
        return

    print(f"➡️ Processing submission {sub_id}")

    serious, moderate, info = categorize_issues(sub)

    if serious or moderate or info:
        subject = f"Vehicle Report - {find_value(sub, 'vehicle') or 'Unknown'}"
        body = format_email(sub, serious, moderate, info)
        sent = send_email(subject, body)

        if not sent:
            print(f"⚠️ Email failed for {sub_id} — will retry next cycle")
            return  # Do NOT mark as processed

    else:
        print(f"ℹ️ No issues found for submission {sub_id}")

    mark_processed_one(sub_id)
    print(f"✅ Marked {sub_id} as processed")

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

        if r.status_code != 200:
            return []

        data = r.json()
        results = data.get("results", [])
        print(f"📦 FETCHED {len(results)} submissions")
        return results

    except Exception as e:
        print("❌ FETCH ERROR:", e)
        return []

# ------------------------
# POLLING MAIN
# Collects all emails first, sends in ONE Gmail session
# ------------------------
def main():
    print("🔁 RUNNING POLLING MAIN")
    create_table()

    processed_ids = get_all_processed_ids()
    subs = sorted(fetch(), key=lambda x: x.get("_id", 0))

    email_queue = []
    no_issue_ids = []

    for sub in subs:
        sub_id = sub.get("_id")

        if not sub_id or sub_id in processed_ids:
            print(f"⏭️ Skipping {sub_id}")
            continue

        print(f"➡️ Queuing {sub_id}")
        serious, moderate, info = categorize_issues(sub)

        if serious or moderate or info:
            subject = f"Vehicle Report - {find_value(sub, 'vehicle') or 'Unknown'}"
            body = format_email(sub, serious, moderate, info)
            email_queue.append((sub_id, subject, body))
        else:
            print(f"ℹ️ No issues for {sub_id}")
            no_issue_ids.append(sub_id)

    sent_ids = send_emails_bulk(email_queue)

    all_done = list(sent_ids) + no_issue_ids
    mark_processed_bulk(all_done)
    print(f"✅ Done. Sent {len(sent_ids)} email(s), marked {len(all_done)} submission(s) as processed.")

    failed = [sub_id for sub_id, _, _ in email_queue if sub_id not in sent_ids]
    if failed:
        print(f"⚠️ {len(failed)} submission(s) failed and will retry next cycle: {failed}")

# ------------------------
# WORKER LOOP
# ------------------------
def run_worker():
    print("🚀 POLLING WORKER STARTED")
    while True:
        try:
            main()
        except Exception as e:
            print("❌ LOOP ERROR:", e)
        time.sleep(300)

# ------------------------
# SELF-PINGER
# ------------------------
SELF_URL = os.getenv("SELF_URL", "")

def pinger_loop():
    if not SELF_URL:
        print("⚠️ SELF_URL not set — self-pinger disabled")
        return
    print(f"📡 PINGER STARTED → {SELF_URL}")
    while True:
        try:
            requests.get(SELF_URL, timeout=6)
            print("📡 Pinged self ✅")
        except Exception as e:
            print(f"📡 Ping failed: {e}")
        time.sleep(270)

# ------------------------
# FLASK
# ------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Kobo Email Service Running ✅"

@app.route("/webhook", methods=["GET", "POST"])
def webhook():

    if request.method == "GET":
        print("✅ Webhook GET verification received")
        return jsonify({"status": "ok"}), 200

    if WEBHOOK_SECRET:
        token = request.headers.get("Authorization", "")
        if token != f"Token {WEBHOOK_SECRET}":
            print("🚫 Unauthorized webhook request")
            return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)

    if not data:
        print("⚠️ Webhook received empty or non-JSON body")
        return jsonify({"error": "Invalid payload"}), 400

    print(f"📩 WEBHOOK RECEIVED — submission ID: {data.get('_id', 'unknown')}")

    try:
        process_submission(data)
    except Exception as e:
        print(f"❌ PROCESS ERROR: {e}")
        import traceback
        traceback.print_exc()

    return jsonify({"status": "received"}), 200

# ------------------------
# START
# ------------------------
if __name__ == "__main__":
    create_table()
    threading.Thread(target=run_worker, daemon=True).start()
    threading.Thread(target=pinger_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)