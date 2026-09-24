import html
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests

UA = {"User-Agent": "Emploi-Moi/1.0 (+job scanner)"}
TIMEOUT = 25


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def clean_html(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def epoch_to_iso(value):
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def get_json(url, params=None):
    response = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def remotive_jobs():
    payload = get_json("https://remotive.com/api/remote-jobs")
    return [
        {
            "title": j.get("title"),
            "company": j.get("company_name"),
            "location": j.get("candidate_required_location"),
            "url": normalize_url(j.get("url")),
            "description": j.get("description") or "",
            "publication_date": j.get("publication_date"),
            "job_type": j.get("job_type"),
            "salary": j.get("salary"),
            "source_job_id": str(j.get("id") or ""),
            "source": "Remotive",
            "category": j.get("category") or "Remote",
            "remote": True,
            "detected_language": j.get("language"),
        }
        for j in payload.get("jobs", [])
    ]


def remoteok_jobs():
    payload = get_json("https://remoteok.com/api")
    jobs = []
    for j in payload if isinstance(payload, list) else []:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        jobs.append(
            {
                "title": j.get("position"),
                "company": j.get("company"),
                "location": j.get("location") or "Remote",
                "url": normalize_url(j.get("url")),
                "description": j.get("description") or "",
                "publication_date": j.get("date") or epoch_to_iso(j.get("epoch")),
                "job_type": "Remote",
                "salary": (
                    f"{j.get('salary_min')} - {j.get('salary_max')}"
                    if j.get("salary_min") and j.get("salary_max")
                    else None
                ),
                "source_job_id": str(j.get("id") or j.get("slug") or ""),
                "source": "Remote OK",
                "category": ", ".join(j.get("tags") or []) or "Remote",
                "remote": True,
                "detected_language": None,
            }
        )
    return jobs


def arbeitnow_jobs(
    endpoint="https://www.arbeitnow.com/api/job-board-api",
    pages=2,
    extra_params=None,
    visa_signal=False,
):
    jobs = []
    for page in range(1, pages + 1):
        params = {"page": page}
        if extra_params:
            params.update(extra_params)
        payload = get_json(endpoint, params=params)
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not data:
            break
        for j in data:
            jobs.append(
                {
                    "title": j.get("title"),
                    "company": j.get("company_name"),
                    "location": j.get("location"),
                    "url": normalize_url(j.get("url")),
                    "description": j.get("description") or "",
                    "publication_date": epoch_to_iso(j.get("created_at")),
                    "job_type": ", ".join(j.get("job_types") or []),
                    "salary": None,
                    "source_job_id": str(j.get("slug") or ""),
                    "source": "Arbeitnow",
                    "category": ", ".join(j.get("tags") or []) or "General",
                    "remote": bool(j.get("remote")),
                    "detected_language": None,
                    "visa_sponsorship_signal": visa_signal,
                }
            )
    return jobs


def fetch_all_sources():
    all_jobs = []
    source_errors = []
    sources = [
        ("Remotive", remotive_jobs),
        ("Remote OK", remoteok_jobs),
        ("Arbeitnow", arbeitnow_jobs),
        (
            "Arbeitnow UK",
            lambda: arbeitnow_jobs(
                "https://www.arbeitnow.co.uk/api/job-board-api",
                pages=2,
            ),
        ),
    ]
    for name, fn in sources:
        try:
            jobs = fn()
            all_jobs.extend(jobs)
            print(f"{name}: {len(jobs)} offres reçues.")
        except requests.RequestException as error:
            source_errors.append(f"{name}: {error}")
            print(f"Erreur source {name}: {error}")
    return all_jobs, source_errors
