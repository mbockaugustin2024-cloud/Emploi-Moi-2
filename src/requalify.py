import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from supabase import create_client
from scanner import calculate_score

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def requalify_row(row):
    score, reasons = calculate_score(row)
    return row["id"], score, "; ".join(reasons)


def main():
    rows = (
        supabase
        .table("jobs")
        .select(
            "id,title,company,location,url,description,publication_date,"
            "job_type,salary,remote,detected_language,score"
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
