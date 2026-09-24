import os
import re
from datetime import datetime, timezone

import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)

REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
MAX_AGE_DAYS = 90
MIN_SCORE = 40

PROFILE_ROLE_TERMS = {
    "customer support": 22,
    "customer service": 22,
    "call center": 22,
    "call-centre": 22,
    "teleprospection": 20,
    "teleprospector": 20,
    "inside sales": 12,
    "sales": 10,
    "manutention": 16,
    "warehouse": 16,
    "logistics": 16,
    "agriculture": 14,
    "restaurant": 14,
    "hospitality": 14,
    "hotel": 14,
    "cleaning": 12,
    "remote": 8,
}

PROFILE_EXPERIENCE_TERMS = {
    "b2b": 6,
    "energy": 6,
    "crm": 6,
    "outbound": 5,
    "inbound": 5,
    "customer": 5,
    "support": 5,
    "follow-up": 4,
    "follow up": 4,
    "relaunch": 4,
    "sales": 4,
}

FRENCH_TERMS = (
    "french",
    "français",
    "francais",
    "francophone",
    "french-speaking",
    "french speaking",
)

REMOTE_TERMS = (
    "remote",
    "work from home",
    "distributed",
    "fully remote",
    "100% remote",
)

RELOCATION_TERMS = (
    "relocation",
    "visa sponsorship",
    "work permit",
    "international applicants",
    "sponsorship available",
)

ENGLISH_HARD_REQUIRED = re.compile(
    r"""
    \b(?:native|fluent|advanced|professional)\s+english\b
    |
    \benglish\s+(?:is\s+)?(?:required|mandatory|essential)\b
    |
    \benglish\s+proficiency\b
    |
    \bc1\s+english\b
    |
    \bc2\s+english\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

ENGLISH_OPTIONAL = re.compile(
    r"""
    english\s+(?:is\s+)?(?:a\s+)?(?:plus|bonus|preferred|optional|nice\s+to\s+have|an\s+advantage)
    |
    english\s+(?:is\s+)?(?:not\s+required)
    """,
    re.IGNORECASE | re.VERBOSE,
)

CLOSED_TERMS = (
    "position has been filled",
    "position filled",
    "no longer accepting applications",
    "applications are closed",
    "job is closed",
    "role is closed",
    "this job has expired",
)


def parse_date(value):
    if not value:
        return None

    value = str(value).strip()

    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(
                value[:-1] + "+00:00"
            )

        parsed = datetime.fromisoformat(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed

    except ValueError:
        return None


def age_in_days(publication_date):
    parsed = parse_date(publication_date)

    if not parsed:
        return None

    age = (
        datetime.now(timezone.utc) - parsed
    ).total_seconds() / 86400

    return age


def is_recent(publication_date):
    age = age_in_days(publication_date)

    if age is None:
        return False

    return 0 <= age <= MAX_AGE_DAYS


def freshness_points(publication_date):
    age = age_in_days(publication_date)

    if age is None:
        return 0

    if age <= 1:
        return 20

    if age <= 3:
        return 17

    if age <= 7:
        return 14

    if age <= 14:
        return 11

    if age <= 30:
        return 8

    if age <= 60:
        return 5

    return 2


def active_status(job):
    description = str(job.get("description") or "").lower()
    url = str(job.get("url") or "").strip()

    for term in CLOSED_TERMS:
        if term in description:
            return False

    if not url:
        return False

    try:
        response = requests.get(
            url,
            timeout=15,
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 JobScanner/1.0"
            },
        )

        # 404/410 = page definitely unavailable.
        if response.status_code in (404, 410):
            return False

        # 403/429 can be protection/rate limiting.
        # We don't mark those jobs as closed.
        return True

    except requests.RequestException:
        # Network failure alone must not delete a potentially valid offer.
        return True


def calculate_score(job):
    title = str(job.get("title") or "")
    description = str(job.get("description") or "")
    location = str(job.get("candidate_required_location") or "")
    language = str(job.get("language") or "")

    searchable_text = (
        f"{title} {description} {location} {language}"
    ).lower()

    if not is_recent(job.get("publication_date")):
        return 0

    if not active_status(job):
        return 0

    score = 0

    for term, points in PROFILE_ROLE_TERMS.items():
        if term in searchable_text:
            score += points

    for term, points in PROFILE_EXPERIENCE_TERMS.items():
        if term in searchable_text:
            score += points

    if any(term in searchable_text for term in FRENCH_TERMS):
        score += 15

    if any(term in searchable_text for term in REMOTE_TERMS):
        score += 7

    if any(term in searchable_text for term in RELOCATION_TERMS):
        score += 8

    english_required = ENGLISH_HARD_REQUIRED.search(searchable_text)
    english_optional = ENGLISH_OPTIONAL.search(searchable_text)

    if english_required and not english_optional:
        return 0

    if english_optional:
        score += 3

    score += freshness_points(job.get("publication_date"))

    return max(0, min(score, 100))


def get_existing_urls():
    existing = set()

    try:
        response = (
            supabase
            .table("jobs")
            .select("url")
            .execute()
        )

        for row in response.data or []:
            url = row.get("url")

            if url:
                existing.add(url)

    except Exception as error:
        print(f"Impossible de lire les offres existantes : {error}")

    return existing


def build_record(job, score):
    publication_date = job.get("publication_date")

    return {
        "title": job.get("title"),
        "company": job.get("company_name"),
        "location": job.get("candidate_required_location"),
        "url": job.get("url"),
        "source": "Remotive",
        "category": job.get("category") or "Remote",
        "remote": True,
        "description": job.get("description"),
        "publication_date": publication_date,
        "job_type": job.get("job_type"),
        "salary": job.get("salary"),
        "source_job_id": str(job.get("id") or ""),
        "score": score,
        "score_reason": (
            "Profil correspondant, offre récente et offre "
            "considérée active au moment du scan."
        ),
        "is_active": True,
        "detected_language": job.get("language"),
        "email_sent": False,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "application_status": "pending",
    }


def main():
    print("Démarrage du scanner...")

    response = requests.get(
        REMOTIVE_URL,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0 JobScanner/1.0"
        },
    )

    response.raise_for_status()

    jobs = response.json().get("jobs", [])

    print(f"{len(jobs)} offres reçues depuis Remotive.")

    existing_urls = get_existing_urls()

    inserted = 0
    skipped_old = 0
    skipped_closed = 0
    skipped_score = 0
    skipped_duplicate = 0

    scored_jobs = []

    for job in jobs:
        publication_date = job.get("publication_date")

        if not is_recent(publication_date):
            skipped_old += 1
            continue

        if not active_status(job):
            skipped_closed += 1
            continue

        score = calculate_score(job)

        if score < MIN_SCORE:
            skipped_score += 1
            continue

        url = job.get("url")

        if not url:
            continue

        if url in existing_urls:
            skipped_duplicate += 1
            continue

        scored_jobs.append(
            (score, publication_date, job)
        )

    # Plus récent d'abord, puis score élevé.
    scored_jobs.sort(
        key=lambda item: (
            parse_date(item[1]) or datetime.min.replace(
                tzinfo=timezone.utc
            ),
            item[0],
        ),
        reverse=True,
    )

    for score, publication_date, job in scored_jobs:
        try:
            record = build_record(job, score)

            supabase.table("jobs").insert(
                record
            ).execute()

            existing_urls.add(job.get("url"))
            inserted += 1

        except Exception as error:
            print(
                f"Erreur insertion pour "
                f"{job.get('title')}: {error}"
            )

    print("----- RÉSULTAT -----")
    print(f"Offres analysées : {len(jobs)}")
    print(f"Nouvelles offres ajoutées : {inserted}")
    print(f"Offres > 90 jours / date invalide : {skipped_old}")
    print(f"Offres fermées/non disponibles : {skipped_closed}")
    print(f"Offres sous 40 % : {skipped_score}")
    print(f"Doublons ignorés : {skipped_duplicate}")


if __name__ == "__main__":
    main()
