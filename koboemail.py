import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import os
import time
from dotenv import load_dotenv

# Load local environment variables if needed (optional on Render)
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

# ------------------------
# DATABASE
# ------------------------
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")  # set on Render

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def create_table():
    """Create table to store processed submission IDs"""
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
    cur.execute("INSERT INTO processed_submissions(submission_id) VALUES (%s) ON CONFLICT DO NOTHING", (sub_id,))
    conn.commit()
    cur.close()
    conn.close()

# ------------------------
# FIELD FINDER
# ------------------------
def find_value(submission, keyword):
    for key, value in submission.items():
        if keyword.lower() in key.lower():
            return value
    return None

# ------------------------
# VALUE NORMALIZER
# ------------------------
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

    # 🔴 SERIOUS
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
    if is_not_ok(find_value(sub, "fan_belts")):
        serious.append("Fan belts condition")
    if is_not_ok(find_value(sub, "coolant_leaks")):
        serious.append("Coolant leakage")
    if is_not_ok(find_value(sub, "sound_of_engine")):
        serious.append("Abnormal engine sound")
    if is_not_ok(find_value(sub, "smoking")):
        serious.append("Engine smoking")

    # 🟠 MODERATE
    if is_not_ok(find_value(sub, "horn_function")):
        moderate.append("Horn not working")
    if is_not_ok(find_value(sub, "indicator")):
        moderate.append("Indicator issue")
    if is_not_ok(find_value(sub, "fan_operation")):
        moderate.append("Fan issue")
    if is_not_ok(find_value(sub, "panel_dashboard")):
        moderate.append("Dashboard light issue")
    if is_not_ok(find_value(sub, "warning_reflective_triangle")):
        moderate.append("Warning triangle missing")
    if is_not_ok(find_value(sub, "brake_hand_brake_function")):
        moderate.append("Hand brake issue")
    if is_not_ok(find_value(sub, "brake_light_function")):
        moderate.append("Brake light issue")
    if is_not_ok(find_value(sub, "headlamp_function")):
        moderate.append("Headlamp issue")
    if is_not_ok(find_value(sub, "tail_light_function")):
        moderate.append("Tail light issue")

    # 🟢 INFO
    if is_not_ok(find_value(sub, "cleanliness")):
        info.append("Cleanliness issue")
    if is_not_ok(find_value(sub, "seat")):
        info.append("Seat issue")
    if is_not_ok(find_value(sub, "windscreen")):
        info.append("Windscreen issue")
    if is_not_ok(find_value(sub, "side_mirror")):
        info.append("Side mirror issue")
    if is_not_ok(find_value(sub, "reflective_sticker")):
        info.append("Reflective sticker issue")
    if is_not_ok(find_value(sub, "jack_wheel_spanner")):
        info.append("Jack & wheel spanner missing")
    if is_not_ok(find_value(sub, "fire_extinguisher")):
        info.append("Fire extinguisher issue")
    if is_not_ok(find_value(sub, "chucks")):
        info.append("Wheel chucks missing")
    if is_not_ok(find_value(sub, "container_belts_and_ropes")):
        info.append("Container belts/ropes issue")

    return serious, moderate, info

# ------------------------
# FORMAT EMAIL
# ------------------------
def format_email(sub, serious, moderate, info):
    vehicle = sub.get("Please_select_Vehicle_Number", "Unknown")
    driver = sub.get("Please_select_you_name", "Unknown")
    vehicle_type = sub.get("Select_Vehicle_type", "Unknown")
    location = sub.get("Select_your_specific_location", "Unknown")
    date_time = sub.get("Enter_date_and_time", str(datetime.now()))

    def make_list(items, color):
        if not items:
            return "<li>None</li>"
        return "".join([f"<li style='color:{color}'>{i}</li>" for i in items])

    return f"""
    <html>
    <body>
        <p>Hello Supervisor,</p>

        <h3>Vehicle Info</h3>
        <ul>
            <li><b>Vehicle:</b> {vehicle}</li>
            <li><b>Driver:</b> {driver}</li>
            <li><b>Type:</b> {vehicle_type}</li>
            <li><b>Location:</b> {location}</li>
            <li><b>Date:</b> {date_time}</li>
        </ul>

        <h3 style="color:red;">Serious Issues</h3>
        <ul>{make_list(serious, "red")}</ul>

        <h3 style="color:orange;">Moderate Issues</h3>
        <ul>{make_list(moderate, "orange")}</ul>

        <h3 style="color:green;">Minor Issues</h3>
        <ul>{make_list(info, "green")}</ul>

        <p>Regards,<br>Fleet System</p>
    </body>
    </html>
    """

# ------------------------
# SEND EMAIL
# ------------------------
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
# FETCH DATA
# ------------------------
def fetch():
    r = requests.get(KOBOTOOLBOX_API_URL, auth=(KOBOTOOLBOX_USERNAME, KOBOTOOLBOX_PASSWORD))
    return r.json().get("results", [])

# ------------------------
# CHECK RECENT (OPTIONAL)
# ------------------------
def is_recent(sub):
    submission_time = sub.get("_submission_time")
    if not submission_time:
        return False
    sub_time = datetime.fromisoformat(submission_time.replace("Z", ""))
    return sub_time > datetime.now() - timedelta(minutes=10)

# ------------------------
# MAIN
# ------------------------
def main():
    create_table()  # ensure DB table exists
    subs = sorted(fetch(), key=lambda x: x.get("_id", 0))

    for sub in subs:
        sub_id = sub.get("_id", 0)

        # ✅ Only process new submissions
        if not is_processed(sub_id):
            serious, moderate, info = categorize_issues(sub)
            subject = f"Vehicle Report - {sub.get('Please_select_Vehicle_Number')}"
            body = format_email(sub, serious, moderate, info)

            send_email(subject, body)
            print(f"✅ Email sent for submission {sub_id}")

            mark_processed(sub_id)  # save in DB

# ------------------------
from flask import Flask
import threading

app = Flask(__name__)

# Run your loop in background
def run_worker():
    while True:
        main()
        time.sleep(300)

@app.route("/")
def home():
    return "Kobo Email Service Running ✅"

if __name__ == "__main__":
    threading.Thread(target=run_worker).start()
    app.run(host="0.0.0.0", port=10000)