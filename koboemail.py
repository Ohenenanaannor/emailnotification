import requests
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

KOBOTOOLBOX_API_URL  = os.getenv("KOBOTOOLBOX_API_URL")
KOBOTOOLBOX_USERNAME = os.getenv("KOBOTOOLBOX_USERNAME")
KOBOTOOLBOX_PASSWORD = os.getenv("KOBOTOOLBOX_PASSWORD")

# Apps Script relay (replaces SMTP)
APPS_SCRIPT_URL    = os.getenv("APPS_SCRIPT_URL")    # the /exec URL from your deployment
APPS_SCRIPT_SECRET = os.getenv("APPS_SCRIPT_SECRET") # same value as in Apps Script code

# Comma-separated supervisor emails, e.g. person1@gmail.com,person2@gmail.com
SUPERVISOR_EMAILS = [
    email.strip()
    for email in os.getenv("SUPERVISOR_EMAILS", "").split(",")
    if email.strip()
]

DATABASE_URL   = os.getenv("DATABASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
SELF_URL       = os.getenv("SELF_URL", "")

# ------------------------
# STARTUP CHECK
# ------------------------
if not SUPERVISOR_EMAILS:
    print("⚠️  WARNING: SUPERVISOR_EMAILS not set — no emails will be sent!")
else:
    print(f"📋 Supervisor emails loaded: {SUPERVISOR_EMAILS}")

if not APPS_SCRIPT_URL:
    print("⚠️  WARNING: APPS_SCRIPT_URL not set — email relay will not work!")

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
            return set(row[0] for row in cur.fetchall())

def is_already_processed(sub_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM processed_submissions WHERE submission_id = %s",
                (sub_id,)
            )
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
    return str(value).lower().strip() in ["not_okay", "no", "false", "bad", "1"]

def is_yes(value):
    if value is None:
        return False
    return str(value).lower().strip() in ["yes", "true", "0"]

# ------------------------
# ISSUE CATEGORIZATION
# ------------------------
def categorize_issues(sub):
    serious, moderate, info = [], [], []

    # 🔴 SERIOUS
    if is_not_ok(find_value(sub, "engine_oil")):        serious.append("Engine oil level")
    if is_not_ok(find_value(sub, "coolant_level")):     serious.append("Coolant level")
    if is_not_ok(find_value(sub, "brake_fluid")):       serious.append("Brake fluid")
    if is_not_ok(find_value(sub, "power_steering")):    serious.append("Power steering oil")
    if is_not_ok(find_value(sub, "exhaust")):           serious.append("Exhaust leakage")
    if is_not_ok(find_value(sub, "tyre")):              serious.append("Tyre condition")
    if is_yes(find_value(sub, "dvla")):                 serious.append("DVLA expired")
    if is_yes(find_value(sub, "road_worthy")):          serious.append("Road worthy expired")
    if is_not_ok(find_value(sub, "fan_belts")):         serious.append("Fan belts condition")
    if is_not_ok(find_value(sub, "coolant_leaks")):     serious.append("Coolant leakage")
    if is_not_ok(find_value(sub, "sound_of_engine")):   serious.append("Abnormal engine sound")
    if is_not_ok(find_value(sub, "smoking")):           serious.append("Engine smoking")

    # 🟠 MODERATE
    if is_not_ok(find_value(sub, "horn_function")):              moderate.append("Horn not working")
    if is_not_ok(find_value(sub, "indicator")):                  moderate.append("Indicator issue")
    if is_not_ok(find_value(sub, "fan_operation")):              moderate.append("Fan issue")
    if is_not_ok(find_value(sub, "panel_dashboard")):            moderate.append("Dashboard light issue")
    if is_not_ok(find_value(sub, "warning_reflective_triangle")):moderate.append("Warning triangle missing")
    if is_not_ok(find_value(sub, "brake_hand_brake_function")): moderate.append("Hand brake issue")
    if is_not_ok(find_value(sub, "brake_light_function")):       moderate.append("Brake light issue")
    if is_not_ok(find_value(sub, "headlamp_function")):          moderate.append("Headlamp issue")
    if is_not_ok(find_value(sub, "tail_light_function")):        moderate.append("Tail light issue")

    # 🟢 INFO
    if is_not_ok(find_value(sub, "cleanliness")):               info.append("Cleanliness issue")
    if is_not_ok(find_value(sub, "seat")):                      info.append("Seat issue")
    if is_not_ok(find_value(sub, "windscreen")):                info.append("Windscreen issue")
    if is_not_ok(find_value(sub, "side_mirror")):               info.append("Side mirror issue")
    if is_not_ok(find_value(sub, "reflective_sticker")):        info.append("Reflective sticker issue")
    if is_not_ok(find_value(sub, "jack_wheel_spanner")):        info.append("Jack & wheel spanner missing")
    if is_not_ok(find_value(sub, "fire_extinguisher")):         info.append("Fire extinguisher issue")
    if is_not_ok(find_value(sub, "chucks")):                    info.append("Wheel chucks missing")
    if is_not_ok(find_value(sub, "container_belts_and_ropes")): info.append("Container belts/ropes issue")

    return serious, moderate, info

# ------------------------
# FORMAT EMAIL
# ------------------------
def format_email(sub, serious, moderate, info):
    vehicle = find_value(sub, "vehicle") or "Unknown"
    driver  = find_value(sub, "name")    or "Unknown"

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
# SEND VIA APPS SCRIPT — single email, used by webhook
# ------------------------
def send_via_apps_script(subject, body):
    if not SUPERVISOR_EMAILS:
        print("⚠️ No supervisor emails configured — skipping send")
        return False

    if not APPS_SCRIPT_URL:
        print("⚠️ APPS_SCRIPT_URL not set — cannot send")
        return False

    payload = {
        "secret":     APPS_SCRIPT_SECRET,
        "recipients": SUPERVISOR_EMAILS,
        "subject":    subject,
        "body":       body
    }

    for attempt in range(2):
        try:
            r = requests.post(APPS_SCRIPT_URL, json=payload, timeout=15)
            result = r.json()
            if result.get("status") == "sent":
                print("📧 EMAIL SENT via Apps Script ✅")
                return True
            else:
                print(f"⚠️ Apps Script returned: {result}")
        except Exception as e:
            print(f"❌ Apps Script attempt {attempt + 1} failed: {e}")
            if attempt == 0:
                print("⏳ Waiting 15s before retry...")
                time.sleep(15)

    return False

# ------------------------
# SEND BULK — used by polling loop
# ------------------------
def send_emails_bulk(email_queue):
    if not email_queue:
        return set()

    sent_ids = set()
    for sub_id, subject, body in email_queue:
        success = send_via_apps_script(subject, body)
        if success:
            sent_ids.add(sub_id)
        time.sleep(2)  # small gap between sends

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
        body    = format_email(sub, serious, moderate, info)
        sent    = send_via_apps_script(subject, body)

        if not sent:
            print(f"⚠️ Email failed for {sub_id} — will retry next cycle")
            return  # Do NOT mark as processed

    else:
        print(f"ℹ️ No issues found for submission {sub_id}")

    mark_processed_one(sub_id)
    print(f"✅ Marked {sub_id} as processed")

# ------------------------
# FETCH FROM KOBOTOOLBOX
# ------------------------
def fetch():
    try:
        print("🌐 Calling Kobo API...")
        all_results = []
        url = f"{KOBOTOOLBOX_API_URL}?limit=500"

        while url:
            r = requests.get(url, auth=(KOBOTOOLBOX_USERNAME, KOBOTOOLBOX_PASSWORD))
            print("🌐 STATUS:", r.status_code)

            if r.status_code != 200:
                print("❌ Bad response from Kobo")
                break

            data    = r.json()
            results = data.get("results", [])
            all_results.extend(results)
            print(f"📦 Fetched {len(results)} (total so far: {len(all_results)})")
            url = data.get("next")

        print(f"📦 TOTAL FETCHED: {len(all_results)} submissions")
        return all_results

    except Exception as e:
        print("❌ FETCH ERROR:", e)
        return []

# ------------------------
# POLLING MAIN
# ------------------------
def main():
    print("🔁 RUNNING POLLING MAIN")
    create_table()

    processed_ids = get_all_processed_ids()
    subs          = sorted(fetch(), key=lambda x: x.get("_id", 0))

    email_queue  = []
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
            body    = format_email(sub, serious, moderate, info)
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
        print(f"⚠️ {len(failed)} submission(s) failed — will retry next cycle: {failed}")

# ------------------------
# WORKER LOOP (every 5 minutes)
# ------------------------
def run_worker():
    print("🚀 POLLING WORKER STARTED")
    while True:
        try:
            main()
        except Exception as e:
            print("❌ LOOP ERROR:", e)
        time.sleep(3600)

# ------------------------
# SELF-PINGER (keeps Render free tier awake)
# ------------------------
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