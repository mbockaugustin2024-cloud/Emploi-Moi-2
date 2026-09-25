import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from supabase import create_client
from sources import fetch_all_sources, normalize_url
from ai_writer import review_job

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config", "profile.json"), "r", encoding="utf-8") as f:
    PROFILE = json.load(f)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

MAX_AGE_DAYS = PROFILE["job_preferences"]["maximum_job_age_days"]
MIN_SCORE = PROFILE["job_preferences"]["minimum_match_score"]
ROLE_TERMS = PROFILE["scoring"]["role_terms"]
ROLE_FAMILIES = PROFILE["scoring"]["role_families"]
TITLE_HARD_EXCLUDE_TERMS = tuple(PROFILE["scoring"]["title_hard_exclude_terms"])
SENIORITY_EXCLUDE_TERMS = tuple(PROFILE["scoring"]["seniority_exclude_terms"])
ALLOWED_SUPERVISION_TITLE_TERMS = tuple(PROFILE["scoring"]["allowed_supervision_title_terms"])
HARD_LANGUAGE_PATTERNS = tuple(PROFILE["scoring"]["hard_language_patterns"])
EXPERIENCE_TERMS = PROFILE["scoring"]["experience_terms"]
FRENCH_BONUS = PROFILE["scoring"]["french_bonus"]
REMOTE_BONUS = PROFILE["scoring"]["remote_bonus"]
RELOCATION_BONUS = PROFILE["scoring"]["relocation_bonus"]
ENGLISH_OPTIONAL_BONUS = PROFILE["scoring"]["english_optional_bonus"]
ENGLISH_HARD_PATTERNS = tuple(PROFILE["scoring"]["english_hard_patterns"])
REMOTE_LOCATION_EXCLUDE_PATTERNS = tuple(PROFILE["scoring"]["remote_location_exclude_patterns"])

ENGLISH_HARD_REQUIRED = re.compile(
    r"\b(?:native|fluent|advanced|professional|excellent|strong|very\s+good|upper\s+intermediate|intermediate)\s+english\b"
    r"|\benglish\s+(?:is\s+)?(?:required|mandatory|essential)\b"
    r"|\benglish\s+(?:fluency|proficiency)\b"
    r"|\b(?:b2|c1|c2)\s+english\b"
    r"|\benglish\s+working\s+proficiency\b"
    r"|\bbusiness\s+english\s+required\b",
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
NON_TARGET_LANGUAGES = (
    "german","italian","spanish","polish","dutch","portuguese",
    "swedish","norwegian","danish","finnish","greek","czech",
    "hungarian","romanian","arabic","mandarin","chinese",
    "japanese","korean","turkish"
)
MULTI_LANGUAGE_TITLE_RE = re.compile(
    r"\b(?:" + "|".join(NON_TARGET_LANGUAGES) + r")\b"
    r".{0,30}\b(?:speaker|speaking|language)\b"
    r"|\b(?:speaker|speaking|language)\b.{0,30}\b(?:" + "|".join(NON_TARGET_LANGUAGES) + r")\b"
    r"|\b(?:french|français|francais|english|anglais)\s*/\s*(?:" + "|".join(NON_TARGET_LANGUAGES) + r")\b"
    r"|\b(?:" + "|".join(NON_TARGET_LANGUAGES) + r")\s*/\s*(?:french|français|francais|english|anglais)\b"
    r"|\b(?:french|français|francais)\s*/\s*(?:english|anglais)\b"
    r"|\b(?:english|anglais)\s*/\s*(?:french|français|francais)\b"
    r"|\b(?:french|français|francais)\s*,\s*(?:english|anglais)\b"
    r"|\b(?:english|anglais)\s*,\s*(?:french|français|francais)\b",
    re.I,
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
        try:
            parsed = parsedate_to_datetime(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, IndexError):
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


def term_matches(text, term):
    pattern = r"(?<!\w)" + re.escape(str(term)).replace(r"\ ", r"\s+") + r"(?!\w)"
    return re.search(pattern, str(text or ""), re.I) is not None


def title_matches_family(title):
    normalized = str(title or "")
    matches = []
    for family in ROLE_FAMILIES:
        if any(term_matches(normalized, term) for term in family["title_terms"]):
            matches.append(family)
    return matches


def remote_location_is_restricted(job):
    if not job.get("remote"):
        return False
    location = str(job.get("location") or "").strip().lower()
    description = str(job.get("description") or "").lower()
    combined = f"{location} {description}"
    if not combined.strip():
        return False

    for pattern in REMOTE_LOCATION_EXCLUDE_PATTERNS:
        if term_matches(combined, pattern):
            return True

    country_names = (
        "canada", "united states", "usa", "uk", "united kingdom",
        "france", "germany", "italy", "spain", "belgium", "netherlands",
        "australia", "new zealand", "ireland", "switzerland",
    )
    restriction_terms = (
        "must be based", "must be located", "must reside",
        "reside in", "based in", "located in", "residence in",
        "remote only", "only",
    )
    if any(term in combined for term in restriction_terms):
        if any(country in combined for country in country_names):
            return True

    if location in country_names:
        return True

    return False


def calculate_score(job):
    if not is_recent(job.get("publication_date")):
        return 0, ["date hors limite"]

    title = str(job.get("title") or "")
    title_lower = title.lower()

    if remote_location_is_restricted(job):
        return 0, ["remote restreint à un pays/territoire"]

    if any(term_matches(title, term) for term in TITLE_HARD_EXCLUDE_TERMS):
        return 0, ["métier hors profil"]

    if (
        any(term_matches(title, term) for term in SENIORITY_EXCLUDE_TERMS)
        and not any(term_matches(title, term) for term in ALLOWED_SUPERVISION_TITLE_TERMS)
    ):
        return 0, ["niveau de poste trop senior/management pour le profil"]

    if any(term_matches(title, pattern) for pattern in HARD_LANGUAGE_PATTERNS) or MULTI_LANGUAGE_TITLE_RE.search(title):
        return 0, ["langue supplémentaire exigée au poste"]

    families = title_matches_family(title)
    if not families:
        return 0, ["aucune famille de métier cible dans le titre"]

    searchable = " ".join(
        str(job.get(key) or "")
        for key in ("title", "description", "location", "detected_language")
    ).lower()

    score = max(family["points"] for family in families)
    reasons = [
        "métier: " + ", ".join(f["name"] for f in families[:2])
    ]

    experience_hits = [
        (term, points)
        for term, points in EXPERIENCE_TERMS.items()
        if term_matches(searchable, term)
    ]
    experience_bonus = sum(points for _, points in experience_hits[:5])
    score += min(experience_bonus, 25)

    if experience_hits:
        reasons.append(
            "expérience: " + ", ".join(term for term, _ in experience_hits[:5])
        )

    if any(term_matches(searchable, term) for term in FRENCH_TERMS):
        score += FRENCH_BONUS
        reasons.append("français")

    if any(term_matches(searchable, term) for term in REMOTE_TERMS) or job.get("remote"):
        score += REMOTE_BONUS
        reasons.append("remote")

    if job.get("visa_sponsorship_signal"):
        score += RELOCATION_BONUS
        reasons.append("visa sponsorship signalisée par la source")
    elif any(term_matches(searchable, term) for term in RELOCATION_TERMS):
        score += RELOCATION_BONUS
        reasons.append("relocation/visa mentionnée")

    if WORK_AUTHORIZATION_HARD.search(searchable):
        return 0, ["autorisation de travail préalable / absence de sponsorship"]

    if OTHER_LANGUAGE_HARD_REQUIRED.search(searchable):
        return 0, ["langue supplémentaire requise à un niveau avancé"]

    if any(term_matches(searchable, pattern) for pattern in ENGLISH_HARD_PATTERNS):
        return 0, ["anglais professionnel/courant ou niveau équivalent requis"]

    if (
        ENGLISH_HARD_REQUIRED.search(searchable)
        and not ENGLISH_OPTIONAL.search(searchable)
    ):
        return 0, ["anglais avancé/fluent requis"]

    if ENGLISH_OPTIONAL.search(searchable):
        score += ENGLISH_OPTIONAL_BONUS
        reasons.append("anglais = plus/optionnel")

    score += freshness_points(job.get("publication_date"))
    reasons.append("fraîcheur")

    return max(0, min(round(score), 100)), reasons


def existing_by_url():
    rows = (
        supabase
        .table("jobs")
        .select("url,score,email_sent,cv_url,cover_letter_url,score_reason")
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

    candidates = []

    for job in jobs:
        url = normalize_url(job.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)

        score, reasons = calculate_score(job)
        if score < MIN_SCORE:
            skipped += 1
            continue

        existing_row = existing.get(url)
        existing_reason = str((existing_row or {}).get("score_reason") or "")
        already_ai_reviewed = "IA REVIEW:" in existing_reason

        candidates.append((job, score, reasons, already_ai_reviewed))

    active_results = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(active_status, job): (job, score, reasons, already_ai_reviewed)
            for job, score, reasons, already_ai_reviewed in candidates
        }
        for future in as_completed(futures):
            job, score, reasons, already_ai_reviewed = futures[future]
            try:
                active_results[normalize_url(job.get("url"))] = (
                    future.result(),
                    job,
                    score,
                    reasons,
                    already_ai_reviewed,
                )
            except Exception as exc:
                print(f"Erreur vérification active {job.get('title')}: {exc}")
                active_results[normalize_url(job.get("url"))] = (
                    True,
                    job,
                    score,
                    reasons,
                    already_ai_reviewed,
                )

    for url, (active, job, score, reasons, already_ai_reviewed) in active_results.items():
        if not active:
            skipped += 1
            continue

        if not already_ai_reviewed:
            review = review_job(job, PROFILE)
            if not review:
                skipped += 1
                print(
                    f"Offre non retenue: lecture IA indisponible ou invalide -> "
                    f"{job.get('title')}"
                )
                continue

            if not bool(review.get("keep")):
                skipped += 1
                print(
                    f"Offre rejetée par lecture IA: {job.get('title')} -> "
                    f"{review.get('reason')}"
                )
                continue

            reasons = [
                "IA REVIEW: OK | "
                + str(review.get("reason") or "").strip()
                + f" | anglais={review.get('english_status')}"
                + f" | remote={review.get('remote_scope')}"
                + f" | work_auth={review.get('work_authorization')}"
            ] + reasons
        else:
            previous = str((existing.get(url) or {}).get("score_reason") or "").strip()
            reasons = ([previous] if previous else []) + reasons

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
