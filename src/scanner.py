import os
import requests
from supabase import create_client

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)

url = "https://remotive.com/api/remote-jobs"
response = requests.get(url, timeout=30)
response.raise_for_status()

jobs = response.json().get("jobs", [])

for job in jobs:
    data = {
        "title": job.get("title"),
        "company": job.get("company_name"),
        "location": job.get("candidate_required_location"),
        "url": job.get("url"),
        "source": "Remotive",
        "category": "Remote",
        "remote": True
    }

    try:
        supabase.table("jobs").insert(data).execute()
    except Exception:
        pass

print(f"{len(jobs)} offres analysées.")
