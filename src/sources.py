import html
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests
import xml.etree.ElementTree as ET

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




def jobicy_jobs():
    payload = get_json(
        "https://jobicy.com/api/v2/remote-jobs",
        params={"count": 100},
    )
    jobs = []
    for j in payload.get("jobs", []) if isinstance(payload, dict) else []:
        if not isinstance(j, dict) or not j.get("jobTitle") or not j.get("url"):
            continue
        salary = None
        if j.get("salaryMin") or j.get("salaryMax"):
            salary = (
                f"{j.get('salaryMin') or ''} - {j.get('salaryMax') or ''} "
                f"{j.get('salaryCurrency') or ''} "
                f"{j.get('salaryPeriod') or ''}"
            ).strip()
        jobs.append(
            {
                "title": j.get("jobTitle"),
                "company": j.get("companyName"),
                "location": j.get("jobGeo") or "Remote",
                "url": normalize_url(j.get("url")),
                "description": j.get("jobDescription") or j.get("jobExcerpt") or "",
                "publication_date": j.get("pubDate"),
                "job_type": ", ".join(j.get("jobType") or [])
                    if isinstance(j.get("jobType"), list)
                    else j.get("jobType"),
                "salary": salary,
                "source_job_id": str(j.get("id") or j.get("jobSlug") or ""),
                "source": "Jobicy",
                "category": ", ".join(j.get("jobIndustry") or [])
                    if isinstance(j.get("jobIndustry"), list)
                    else str(j.get("jobIndustry") or "Remote"),
                "remote": True,
                "detected_language": None,
            }
        )
    return jobs



def himalayas_jobs():
    jobs = []
    queries = [
        "customer support",
        "customer service",
        "call center",
        "sales",
        "teleprospection",
    ]
    seen = set()

    for query in queries:
        try:
            payload = get_json(
                "https://himalayas.app/jobs/api/search",
                params={"q": query, "sort": "recent", "page": 1},
            )
            for j in payload.get("jobs", []) if isinstance(payload, dict) else []:
                if not isinstance(j, dict):
                    continue

                url = normalize_url(j.get("applicationLink"))
                guid = str(j.get("guid") or url or "")
                if not url or guid in seen:
                    continue
                seen.add(guid)

                restrictions = j.get("locationRestrictions") or []
                location = (
                    ", ".join(str(x) for x in restrictions)
                    if restrictions
                    else "Worldwide / Remote"
                )

                description = j.get("description") or j.get("excerpt") or ""
                salary = None
                if j.get("minSalary") is not None or j.get("maxSalary") is not None:
                    salary = (
                        f"{j.get('minSalary') or ''} - {j.get('maxSalary') or ''} "
                        f"{j.get('currency') or ''} {j.get('salaryPeriod') or 'annual'}"
                    ).strip()

                jobs.append(
                    {
                        "title": j.get("title"),
                        "company": j.get("companyName"),
                        "location": location,
                        "url": url,
                        "description": description,
                        "publication_date": j.get("pubDate"),
                        "job_type": j.get("employmentType"),
                        "salary": salary,
                        "source_job_id": guid,
                        "source": "Himalayas",
                        "category": (
                            ", ".join(j.get("category") or [])
                            if isinstance(j.get("category"), list)
                            else str(j.get("category") or "Remote")
                        ),
                        "remote": True,
                        "detected_language": None,
                    }
                )
        except Exception as error:
            print(f"Himalayas query '{query}': {error}")

    return jobs


def _rss_items(url, source_name, category_name):
    response = requests.get(url, headers=UA, timeout=TIMEOUT)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    jobs = []

    for item in root.findall(".//item"):
        title = item.findtext("title")
        link = item.findtext("link")
        description = item.findtext("description") or ""
        pub_date = item.findtext("pubDate")
        guid = item.findtext("guid") or link

        if not title or not link:
            continue

        jobs.append(
            {
                "title": title,
                "company": None,
                "location": "Remote — see listing",
                "url": normalize_url(link),
                "description": description,
                "publication_date": pub_date,
                "job_type": "Remote",
                "salary": None,
                "source_job_id": str(guid or ""),
                "source": source_name,
                "category": category_name,
                "remote": True,
                "detected_language": None,
            }
        )

    return jobs


def weworkremotely_jobs():
    feeds = [
        (
            "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
            "Customer Support",
        ),
        (
            "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
            "Sales and Marketing",
        ),
    ]
    jobs = []

    for url, category in feeds:
        try:
            jobs.extend(_rss_items(url, "We Work Remotely", category))
        except Exception as error:
            print(f"We Work Remotely ({category}): {error}")

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
        ("Jobicy", jobicy_jobs),
        ("Himalayas", himalayas_jobs),
        ("We Work Remotely", weworkremotely_jobs),
        ("Arbeitnow", arbeitnow_jobs),
        (
            "Arbeitnow Visa Sponsorship",
            lambda: arbeitnow_jobs(
                "https://www.arbeitnow.com/api/job-board-api",
                pages=2,
                extra_params={"visa_sponsorship": "true"},
                visa_signal=True,
            ),
        ),
    ]
    for name, fn in sources:
        try:
            jobs = fn()
            all_jobs.extend(jobs)
            print(f"{name}: {len(jobs)} offres reçues.")
        except Exception as error:
            source_errors.append(f"{name}: {error}")
            print(f"Erreur source {name}: {error}")
    return all_jobs, source_errors
