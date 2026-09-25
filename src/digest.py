import html
import os
from datetime import datetime, timedelta, timezone

import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
BREVO_EMAIL = os.environ["BREVO_EMAIL"]

MIN_SCORE = 40

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


def esc(value):
    return html.escape(str(value or ""))


def job_block(job, index):
    title = esc(job.get("title") or "Poste sans titre")
    company = esc(job.get("company") or "Entreprise non précisée")
    location = esc(job.get("location") or "Non précisé")
    score = job.get("score") or 0
    job_url = job.get("url") or ""
    cv_url = job.get("cv_url") or ""
    cover_url = job.get("cover_letter_url") or ""

    links = []
    if job_url:
        links.append(
            f'<a href="{esc(job_url)}">Voir l’offre / postuler</a>'
        )
    if cv_url:
        links.append(
            f'<a href="{esc(cv_url)}">CV correspondant</a>'
        )
    if cover_url:
        links.append(
            f'<a href="{esc(cover_url)}">Lettre correspondante</a>'
        )

    links_html = " &nbsp;|&nbsp; ".join(links)

    return f"""
    <div style="margin:0 0 22px;padding:16px;border:1px solid #ddd;border-radius:8px;">
      <div style="font-size:18px;font-weight:700;">{index}. {title}</div>
      <div style="margin-top:6px;"><b>Entreprise :</b> {company}</div>
      <div><b>Lieu :</b> {location}</div>
      <div><b>Score d’adéquation estimé :</b> {score}%</div>
      <div style="margin-top:10px;">{links_html or "Lien non disponible."}</div>
    </div>
    """


def main():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    response = (
        supabase
        .table("jobs")
        .select(
            "id,title,company,location,url,score,cv_url,cover_letter_url,"
            "publication_date,source"
        )
        .eq("is_active", True)
        .eq("email_sent", False)
        .gte("score", MIN_SCORE)
        .ilike("score_reason", "%IA REVIEW: OK%")
        .gte("publication_date", cutoff)
        .order("score", desc=True)
        .order("publication_date", desc=True)
        .execute()
    )

    candidates = response.data or []
    jobs = [
        job for job in candidates
        if job.get("cv_url") and job.get("cover_letter_url")
    ]

    if not jobs:
        print("Aucune nouvelle offre éligible pour le digest.")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    blocks = [
        job_block(job, index)
        for index, job in enumerate(jobs, start=1)
    ]

    html_content = f"""
    <!doctype html>
    <html>
      <body style="font-family:Arial,sans-serif;line-height:1.5;color:#222;">
        <h1>Emploi-Moi — nouvelles offres</h1>
        <p>
          Digest du {today} : <b>{len(jobs)}</b> nouvelle(s) offre(s)
          correspondant au seuil de {MIN_SCORE}%.
        </p>
        <p style="font-size:13px;color:#666;">
          Le score est un score d’adéquation estimé au profil, pas une
          probabilité réelle d’embauche.
        </p>
        {"".join(blocks)}
      </body>
    </html>
    """

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
        "subject": f"Emploi-Moi — {len(jobs)} nouvelle(s) offre(s)",
        "htmlContent": html_content
    }

    send_response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json"
        },
        json=payload,
        timeout=30
    )

    print(
        f"Brevo HTTP {send_response.status_code}: "
        f"{send_response.text[:500]}"
    )
    send_response.raise_for_status()

    now = datetime.now(timezone.utc).isoformat()

    for job in jobs:
        (
            supabase
            .table("jobs")
            .update({
                "email_sent": True,
                "emailed_at": now
            })
            .eq("id", job["id"])
            .execute()
        )

    print(f"Digest envoyé pour {len(jobs)} offre(s).")


if __name__ == "__main__":
    main()
