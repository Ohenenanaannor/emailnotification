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

KOBOTOOLBOX_API_URL = "https://kf.kobotoolbox.org/api/v2/assets/ahCr8ALo67qrBdaRjQkm8K/data/"
KOBOTOOLBOX_USERNAME = "annorpoku"
KOBOTOOLBOX_PASSWORD = os.getenv("KOBOTOOLBOX_PASSWORD")

SMTP_SERVER = "smtp.infomaniak.com"
SMTP_PORT = 587
EMAIL_USER = "ohenenana.annor@raincoatroofingsystems.com"
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SUPERVISOR_EMAIL = "ohenenanaannor2000@gmail.com"

DATABASE_URL = os.getenv("DATABASE_URL")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")  # leave blank in .env to skip verification

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
# EMAIL
# ------------------------
def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = SUPERVISOR_EMAIL
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.set_debuglevel(1)
            server.starttls()
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
# PROCESS ONE SUBMISSION
# Used by both webhook and polling loop
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
        send_email(subject, body)
    else:
        print(f"ℹ️ No issues found for submission {sub_id}")

    mark_processed_one(sub_id)

# ------------------------
# FETCH KOBO (for polling)
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
# POLLING MAIN (fallback)
# ------------------------
def main():
    print("🔁 RUNNING POLLING MAIN")

    create_table()

    processed_ids = get_all_processed_ids()
    subs = sorted(fetch(), key=lambda x: x.get("_id", 0))

    new_processed_ids = []

    for sub in subs:
        sub_id = sub.get("_id")

        if not sub_id or sub_id in processed_ids:
            print(f"⏭️ Skipping {sub_id}")
            continue

        print(f"➡️ Processing {sub_id}")

        serious, moderate, info = categorize_issues(sub)

        if serious or moderate or info:
            subject = f"Vehicle Report - {find_value(sub, 'vehicle') or 'Unknown'}"
            body = format_email(sub, serious, moderate, info)
            send_email(subject, body)
        else:
            print("ℹ️ No issues")

        new_processed_ids.append(sub_id)

    mark_processed_bulk(new_processed_ids)

# ------------------------
# WORKER LOOP (fallback polling every 5 mins)
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
# SELF-PINGER (keeps Render awake)
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
        time.sleep(270)  # every 4.5 min — safely under Render's 5-min sleep threshold

# ------------------------
# FLASK
# ------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Kobo Email Service Running ✅"


@app.route("/webhook", methods=["GET", "POST"])  # GET handles Kobo's endpoint verification
def webhook():

    # Kobo sends a GET request first to verify the endpoint is alive
    if request.method == "GET":
        print("✅ Webhook GET verification received")
        return jsonify({"status": "ok"}), 200

    # --- Optional secret token verification ---
    if WEBHOOK_SECRET:
        token = request.headers.get("Authorization", "")
        if token != f"Token {WEBHOOK_SECRET}":
            print("🚫 Unauthorized webhook request")
            return jsonify({"error": "Unauthorized"}), 401

    # --- Parse payload ---
    data = request.get_json(silent=True)

    if not data:
        print("⚠️ Webhook received empty or non-JSON body")
        return jsonify({"error": "Invalid payload"}), 400

    print(f"📩 WEBHOOK RECEIVED — submission ID: {data.get('_id', 'unknown')}")

    # --- Process in background so we return 200 fast ---
    threading.Thread(target=process_submission, args=(data,), daemon=True).start()

    return jsonify({"status": "received"}), 200


# ------------------------
# START
# ------------------------
if __name__ == "__main__":
    create_table()
    threading.Thread(target=run_worker, daemon=True).start()
    threading.Thread(target=pinger_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)