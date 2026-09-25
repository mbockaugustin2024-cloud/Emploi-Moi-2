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
    max_reviews = max(1, int(os.environ.get("MAX_AI_REQUALIFY", "20")))

    rows = (
        supabase
        .table("jobs")
        .select(
            "id,title,company,location,url,description,publication_date,"
            "job_type,salary,remote,detected_language,score,score_reason"
        )
        .eq("is_active", True)
        .order("score", desc=True)
        .limit(max_reviews)
        .execute()
        .data
        or []
    )

    print(f"Offres à requalifier (plafond {max_reviews}): {len(rows)}")
    changed = 0

    for row in rows:
        try:
            job_id, score, reason = requalify_row(row)
            old_score = row.get("score")

            if old_score != score:
                supabase.table("jobs").update(
                    {
                        "score": score,
                        "score_reason": reason,
                    }
                ).eq("id", job_id).execute()
                changed += 1

            print(f"Requalification: {row.get("title")} -> score={score}")

        except Exception as exc:
            print(f"Erreur requalification {row.get("title")}: {exc}")
            continue

        if "indisponible" in str(reason).lower():
            print("IA indisponible: arrêt pour préserver les quotas.")
            break

    print(f"Scores modifiés: {changed}")


if __name__ == "__main__":
    main()
