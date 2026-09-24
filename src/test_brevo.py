import os
import requests

BREVO_API_KEY = os.environ["BREVO_API_KEY"]
BREVO_EMAIL = os.environ["BREVO_EMAIL"]

payload = {
    "sender": {
        "email": BREVO_EMAIL,
        "name": "Emploi-Moi"
    },
    "to": [
        {
            "email": BREVO_EMAIL
        }
    ],
    "subject": "Emploi-Moi — test Brevo réussi",
    "htmlContent": """
    <html>
      <body style="font-family:Arial,sans-serif;">
        <h2>Test Brevo Emploi-Moi</h2>
        <p>Ce message confirme que GitHub Actions peut utiliser ta clé Brevo
        et ton adresse vérifiée pour envoyer un email.</p>
      </body>
    </html>
    """
}

response = requests.post(
    "https://api.brevo.com/v3/smtp/email",
    headers={
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    },
    json=payload,
    timeout=30
)

print(f"Brevo HTTP {response.status_code}")
print(response.text[:500])
response.raise_for_status()
print("TEST_BREVO_OK")
