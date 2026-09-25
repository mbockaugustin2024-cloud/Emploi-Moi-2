import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from supabase import create_client
from scanner import calculate_score
from ai_writer import review_job

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(
    os.path.join(BASE_DIR, "config", "profile.json"),
    "r",
    encoding="utf-8",
) as f:
    PROFILE = json.load(f)


def requalify_row(row):
    score, reasons = calculate_score(row)
    if score < 40:
        return row["id"], score, "; ".join(reasons)

    review = review_job(row, PROFILE)
    if not review:
        return row["id"], 0, "IA REVIEW: indisponible/invalide"
    if not bool(review.get("keep")):
        return (
            row["id"],
            0,
            "IA REVIEW: REJECT | " + str(review.get("reason") or ""),
        )

    reasons = [
        "IA REVIEW: OK | "
        + str(review.get("reason") or "")
        + f" | anglais={review.get('english_status')}"
        + f" | remote={review.get('remote_scope')}"
        + f" | work_auth={review.get('work_authorization')}"
    ] + reasons
    return row["id"], score, "; ".join(reasons)


def main():
    rows = (
        supabase
        .table("jobs")
        .select(
            "id,title,company,location,url,description,publication_date,"
            "job_type,salary,remote,detected_language,score,score_reason"
        )
        .execute()
        .data
        or []
    )

    print(f"Offres à requalifier: {len(rows)}")
    changed = 0
    eligible = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [
            executor.submit(requalify_row, row)
            for row in rows
        ]
        for future in as_completed(futures):
            job_id, score, reason = future.result()
            row = next((item for item in rows if item["id"] == job_id), None)
            if row is None:
                continue
            old_score = row.get("score")
            if score >= 40:
                eligible.append(row.get("title") or "Sans titre")
            if old_score == score:
                continue
            supabase.table("jobs").update(
                {
                    "score": score,
                    "score_reason": reason,
                }
            ).eq("id", job_id).execute()
            changed += 1

    print(f"Scores modifiés: {changed}")
    print(f"Offres >= 40 après requalification: {len(eligible)}")
    for title in sorted(eligible)[:20]:
        print(f"Éligible: {title}")


if __name__ == "__main__":
    main()
