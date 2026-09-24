import json
import os
import re
from datetime import datetime, timezone

import requests
from supabase import create_client
from sources import fetch_all_sources, normalize_url

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config", "profile.json"), "r", encoding="utf-8") as f:
    PROFILE = json.load(f)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

MAX_AGE_DAYS = PROFILE["job_preferences"]["maximum_job_age_days"]
MIN_SCORE = PROFILE["job_preferences"]["minimum_match_score"]
ROLE_TERMS = PROFILE["scoring"]["role_terms"]
EXPERIENCE_TERMS = PROFILE["scoring"]["experience_terms"]
FRENCH_BONUS = PROFILE["scoring"]["french_bonus"]
REMOTE_BONUS = PROFILE["scoring"]["remote_bonus"]
RELOCATION_BONUS = PROFILE["scoring"]["relocation_bonus"]
ENGLISH_OPTIONAL_BONUS = PROFILE["scoring"]["english_optional_bonus"]

ENGLISH_HARD_REQUIRED = re.compile(
    r"\b(?:native|fluent|advanced|professional)\s+english\b"
    r"|\benglish\s+(?:is\s+)?(?:required|mandatory|essential)\b"
    r"|\benglish\s+proficiency\b|\bc1\s+english\b|\bc2\s+english\b",
    re.I,
)
ENGLISH_OPTIONAL = re.compile(
    r"english\s+(?:is\s+)?(?:a\s+)?(?:plus|bonus|preferred|optional|nice\s+to\s+have|an\s+advantage)"
    r"|english\s+(?:is\s+)?(?:not\s+required)",
    re.I,
)
OTHER_LANGUAGE_HARD_REQUIRED = re.compile(
    r"\b(?:native|fluent|advanced|professional)\s+(?:german|italian|spanish)\b"
    r"|\b(?:german|italian|spanish)\s+(?:is\s+)?(?:required|mandatory|essential)\b",
    re.I,
)
WORK_AUTHORIZATION_HARD = re.compile(
    r"no\s+visa\s+sponsorship"
    r"|visa\s+sponsorship\s+(?:is\s+)?(?:not\s+available|unavailable)"
    r"|must\s+be\s+(?:legally\s+)?authorized\s+to\s+work"
    r"|legally\s+authorized\s+to\s+work"
    r"|work\s+authorization\s+(?:is\s+)?required"
    r"|right\s+to\s+work\s+(?:is\s+)?required"
    r"|must\s+have\s+(?:the\s+)?right\s+to\s+work",
    re.I,
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


def parse_date(value):
    if not value:
        return None
    value = str(value).strip()
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age_in_days(value):
    parsed = parse_date(value)
    if not parsed:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400


def is_recent(value):
    age = age_in_days(value)
    return age is not None and 0 <= age <= MAX_AGE_DAYS


def freshness_points(value):
    age = age_in_days(value)
    if age is None:
        return 0
    for limit, points in (
        (1, 20), (3, 17), (7, 14), (14, 11),
        (30, 8), (60, 5), (90, 2),
    ):
        if age <= limit:
            return points
    return 0


def active_status(job):
    description = str(job.get("description") or "").lower()
    url = normalize_url(job.get("url") or "")
    if not url:
        return False
    if any(term in description for term in CLOSED_TERMS):
        return False
    try:
        response = requests.get(
            url,
            timeout=12,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 Emploi-Moi/1.0"},
        )
        return response.status_code not in (404, 410)
    except requests.RequestException:
        return True


def calculate_score(job):
    if not is_recent(job.get("publication_date")):
        return 0, ["date hors limite"]

    searchable = " ".join(
        str(job.get(key) or "")
        for key in ("title", "description", "location", "detected_language")
    ).lower()

    score = freshness_points(job.get("publication_date"))
    reasons = []

    role_hits = [
        (term, points)
        for term, points in ROLE_TERMS.items()
        if term in searchable
    ]
    experience_hits = [
        (term, points)
        for term, points in EXPERIENCE_TERMS.items()
        if term in searchable
    ]

    score += sum(points for _, points in role_hits)
    score += sum(points for _, points in experience_hits)

    if role_hits:
        reasons.append(
            "rôles: " + ", ".join(term for term, _ in role_hits[:4])
        )
    if experience_hits:
        reasons.append(
            "expérience: "
            + ", ".join(term for term, _ in experience_hits[:5])
        )

    if any(term in searchable for term in FRENCH_TERMS):
        score += FRENCH_BONUS
        reasons.append("français")

    if any(term in searchable for term in REMOTE_TERMS) or job.get("remote"):
        score += REMOTE_BONUS
        reasons.append("remote")

    if job.get("visa_sponsorship_signal"):
        score += RELOCATION_BONUS
        reasons.append("visa sponsorship signalisée par la source")
    elif any(term in searchable for term in RELOCATION_TERMS):
        score += RELOCATION_BONUS
        reasons.append("relocation/visa mentionnée")

    if WORK_AUTHORIZATION_HARD.search(searchable):
        return 0, ["autorisation de travail préalable / absence de sponsorship"]

    if OTHER_LANGUAGE_HARD_REQUIRED.search(searchable):
        return 0, ["langue supplémentaire requise à un niveau avancé"]

    if (
        ENGLISH_HARD_REQUIRED.search(searchable)
        and not ENGLISH_OPTIONAL.search(searchable)
    ):
        return 0, ["anglais avancé/fluent requis"]

    if ENGLISH_OPTIONAL.search(searchable):
        score += ENGLISH_OPTIONAL_BONUS
        reasons.append("anglais = plus/optionnel")

    return max(0, min(round(score), 100)), reasons


def existing_by_url():
    rows = (
        supabase
        .table("jobs")
        .select("url,score,email_sent,cv_url,cover_letter_url")
        .execute()
        .data
        or []
    )
    return {
        normalize_url(row.get("url")): row
        for row in rows
        if row.get("url")
    }


def build_record(job, score, reasons):
    return {
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "url": normalize_url(job.get("url")),
        "source": job.get("source"),
        "category": job.get("category"),
        "remote": bool(job.get("remote")),
        "description": job.get("description"),
        "publication_date": job.get("publication_date"),
        "job_type": job.get("job_type"),
        "salary": job.get("salary"),
        "source_job_id": job.get("source_job_id"),
        "score": score,
        "score_reason": (
            "; ".join(reasons)
            if reasons
            else "Correspondance de profil et fraîcheur vérifiées."
        ),
        "is_active": True,
        "detected_language": job.get("detected_language"),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "application_status": "pending",
    }


def main():
    print("Démarrage du scanner multi-source...")
    jobs, errors = fetch_all_sources()
    print(f"Total brut: {len(jobs)}")

    existing = existing_by_url()
    seen = set()
    inserted = 0
    updated = 0
    skipped = 0

    for job in jobs:
        url = normalize_url(job.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)

        score, reasons = calculate_score(job)
        if score < MIN_SCORE:
            skipped += 1
            continue

        if not active_status(job):
            skipped += 1
            continue

        record = build_record(job, score, reasons)
        try:
            if url in existing:
                (
                    supabase
                    .table("jobs")
                    .update(record)
                    .eq("url", url)
                    .execute()
                )
                updated += 1
            else:
                supabase.table("jobs").insert(record).execute()
                inserted += 1
        except Exception as exc:
            print(f"Erreur DB pour {job.get('title')}: {exc}")

    print("----- RÉSULTAT -----")
    print(f"Offres brutes: {len(jobs)}")
    print(f"Nouvelles: {inserted}")
    print(f"Actualisées: {updated}")
    print(f"Écartées: {skipped}")

    if errors:
        print("Erreurs sources:")
        for error in errors:
            print(error)


if __name__ == "__main__":
    main()
