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

# Load environment variables
load_dotenv()

# ------------------------
# CONFIG
# ------------------------
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
    return psycopg2.connect(DATABASE_URL)

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
    return value in ["not_okay", "no", "1", "false", "bad"]

def is_yes(value):
    if value is None:
        return False
    value = str(value).lower().strip()
    return value in ["yes", "0", "true"]

# ------------------------
# ISSUE CATEGORIZATION
# ------------------------
def categorize_issues(sub):
    serious, moderate, info = [], [], []

    if is_not_ok(find_value(sub, "engine_oil")):
        serious.append("Engine oil level")
    if is_not_ok(find_value(sub, "coolant_level")):
        serious.append("Coolant level")
    if is_not_ok(find_value(sub, "brake_fluid")):
        serious.append("Brake fluid")
    if is_not_ok(find_value(sub, "power_steering")):
        serious.append("Power steering oil")
    if is_not_ok(find_value(sub, "exhaust")):
        serious.append("Exhaust leakage")
    if is_not_ok(find_value(sub, "tyre")):
        serious.append("Tyre condition")
    if is_yes(find_value(sub, "dvla")):
        serious.append("DVLA expired")
    if is_yes(find_value(sub, "road_worthy")):
        serious.append("Road worthy expired")

    if is_not_ok(find_value(sub, "horn_function")):
        moderate.append("Horn not working")

    if is_not_ok(find_value(sub, "cleanliness")):
        info.append("Cleanliness issue")

    return serious, moderate, info

# ------------------------
# EMAIL
# ------------------------
def format_email(sub, serious, moderate, info):
    vehicle = sub.get("Please_select_Vehicle_Number", "Unknown")
    driver = sub.get("Please_select_you_name", "Unknown")

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

def send_email(subject, body):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = SUPERVISOR_EMAIL
    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)

# ------------------------
# FETCH
# ------------------------
def fetch():
    r = requests.get(KOBOTOOLBOX_API_URL, auth=(KOBOTOOLBOX_USERNAME, KOBOTOOLBOX_PASSWORD))
    return r.json().get("results", [])

# ------------------------
# MAIN LOGIC
# ------------------------
def main():
    create_table()
    subs = sorted(fetch(), key=lambda x: x.get("_id", 0))

    for sub in subs:
        sub_id = sub.get("_id", 0)

        if not is_processed(sub_id):
            serious, moderate, info = categorize_issues(sub)

            subject = f"Vehicle Report - {sub.get('Please_select_Vehicle_Number')}"
            body = format_email(sub, serious, moderate, info)

            send_email(subject, body)
            print(f"✅ Sent {sub_id}")

            mark_processed(sub_id)

# ------------------------
# BACKGROUND LOOP
# ------------------------
def run_worker():
    while True:
        try:
            main()
        except Exception as e:
            print("❌ Error:", e)

        time.sleep(300)  # 5 minutes

# ------------------------
# FLASK APP
# ------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Kobo Email Service Running ✅"

# ------------------------
# START
# ------------------------
if __name__ == "__main__":
    threading.Thread(target=run_worker).start()
    app.run(host="0.0.0.0", port=10000)