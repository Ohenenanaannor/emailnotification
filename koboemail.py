import smtplib
from email.mime.text import MIMEText

SMTP_SERVER = "smtp.infomaniak.com"
SMTP_PORT = 465
EMAIL_USER = "ohenenana.annor@raincoatroofingsystems.com"
EMAIL_PASSWORD = "YOUR_PASSWORD"  # or load from env
SUPERVISOR_EMAIL = "ohenenanaannor2000@gmail.com"

msg = MIMEText("Test email from Kobo Email Service", "plain")
msg["Subject"] = "Test Email"
msg["From"] = EMAIL_USER
msg["To"] = SUPERVISOR_EMAIL

try:
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
    print("✅ Email sent successfully!")
except Exception as e:
    print("❌ Email failed:", e)