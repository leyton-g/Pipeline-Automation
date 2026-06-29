import os
import time
import requests
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from pydantic import BaseModel
from pyairtable import Api as AirtableApi
from google import genai
from google.genai import types

# ── Clients (pulled from Vercel env vars) ────────────────────────────────────
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

airtable        = AirtableApi(os.getenv("AIRTABLE_API_KEY"))
company_table   = airtable.table(os.getenv("AIRTABLE_BASE_ID"), os.getenv("AIRTABLE_TABLE_NAME"))
scoring_table   = airtable.table(os.getenv("AIRTABLE_BASE_ID"), os.getenv("AIRTABLE_SCORING_TABLE_NAME"))

FIREFLIES_API_KEY = os.getenv("FIREFLIES_API_KEY")

app = FastAPI()

# ── Models ────────────────────────────────────────────────────────────────────
class WebhookPayload(BaseModel):
    eventType: str
    meetingId: str
    title: str

# ── Helpers ───────────────────────────────────────────────────────────────────
def get_live_airtable_scoring_reference() -> str:
    """Pull scoring rules from your Airtable scoring/reference table."""
    records = scoring_table.all()
    if not records:
        return "No scoring reference found."
    # Adjust 'Rules' to whatever your column is actually named
    return "\n".join(r["fields"].get("Rules", "") for r in records)


def get_fireflies_transcript(meeting_id: str) -> str:
    """Fetch transcript text from Fireflies GraphQL API."""
    query = """
    query Transcript($id: String!) {
      transcript(id: $id) {
        sentences {
          text
          speaker_name
        }
      }
    }
    """
    resp = requests.post(
        "https://api.fireflies.ai/graphql",
        json={"query": query, "variables": {"id": meeting_id}},
        headers={
            "Authorization": f"Bearer {FIREFLIES_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    sentences = data.get("data", {}).get("transcript", {}).get("sentences", [])
    if not sentences:
        return ""
    return "\n".join(
        f"{s.get('speaker_name', 'Unknown')}: {s.get('text', '')}"
        for s in sentences
    )


# ── AI Scoring ────────────────────────────────────────────────────────────────
def analyze_and_score_with_gemini(
    airtable_notes: str, fireflies_transcript: str, scoring_reference: str
) -> str | None:
    print("\n🧠 [AI CORE ENGINE] Scoring with Low-Inflation Calibration...")

    system_instruction = (
        "You are an elite, highly skeptical venture capital investment analyst at Tech Square Ventures. "
        "Your task is to mathematically score opportunities on a strict 0 to 10 scale. "
        "A score of 10 represents absolute perfection with zero risks. "
        "A score of 7-8 represents a strong institutional-grade deal. Avoid grade inflation."
    )

    prompt_body = f"""
Evaluate this opportunity strictly using a 0 to 10 scale based on the criteria weights below.

[GOLD STANDARD SCORING ANCHOR]
- A startup with elite founders (MIT/Harvard/McKinsey), $140B TAM, and 3 enterprise pilots was graded 7.78–7.83.
- Alignment 6.5 (competing-lead friction), Team 7.5 (no prior exit), Region 7.0 (HQ unverified).
Apply this exact level of skepticism.

DATA SOURCE 1: Scoring Rules
\"\"\"{scoring_reference}\"\"\"

DATA SOURCE 2: CRM Notes
\"\"\"{airtable_notes}\"\"\"

DATA SOURCE 3: Meeting Transcript
\"\"\"{fireflies_transcript}\"\"\"

CRITERIA & WEIGHTS:
1. Alignment 20% | 2. Market 20% | 3. Team 25% | 4. Business Model 10% | 5. Sector 10% | 6. Region 15%

OUTPUT: Return ONLY a single plain number rounded to two decimal places (e.g. 7.78). No markdown, no sentences.
"""

    delay = 2
    for attempt in range(3):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt_body,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                ),
            )
            raw = response.text.strip()
            # Validate it's actually a number before returning
            float(raw)
            return raw
        except ValueError:
            print(f"❌ [PARSE ERROR] Gemini returned non-numeric: '{response.text.strip()}'")
            return None
        except Exception as e:
            if "503" in str(e) and attempt < 2:
                time.sleep(delay)
                delay *= 2
            else:
                print(f"❌ [API ERROR] Gemini failed: {e}")
                return None


# ── Pipeline ──────────────────────────────────────────────────────────────────
def process_pipeline(meeting_id: str, company_target: str) -> str:
    print(f"\n🚀 [PIPELINE] Starting for: {company_target} | Meeting: {meeting_id}")

    scoring_rules = get_live_airtable_scoring_reference()

    formula = f"{{Name}} = '{company_target}'"
    records = company_table.all(formula=formula)
    print(f"🔍 [AIRTABLE] Formula: {formula} | Records found: {len(records)}")

    if not records:
        print(f"⚠️ [NOT FOUND] No Airtable row for '{company_target}'")
        return "Company not found."

    record = records[0]
    record_id = record["id"]
    historical_notes = record["fields"].get("Notes", "")
    print(f"✅ [AIRTABLE] Matched record {record_id}")

    live_transcript = get_fireflies_transcript(meeting_id)
    if not live_transcript:
        print("⚠️ [FIREFLIES] Empty transcript.")
        return "Empty transcript."

    score = analyze_and_score_with_gemini(historical_notes, live_transcript, scoring_rules)

    if score:
        # Score field is a Number type in Airtable — write as float
        company_table.update(record_id, {"Score": float(score)})
        print(f"💾 [AIRTABLE WRITE] Score {score} saved to {company_target}")
        return score

    return "Scoring failed."


# ── Webhook ───────────────────────────────────────────────────────────────────
@app.post("/webhooks/fireflies")
async def handle_webhook(
    payload: WebhookPayload,
    background_tasks: BackgroundTasks,
    x_tsv_token: str = Header(None),
):
    if x_tsv_token != os.getenv("TSV_WEBHOOK_SECRET", "my-secret-handshake"):
        raise HTTPException(status_code=401, detail="Invalid token")

    company_target = payload.title.split(" x ")[0].strip()

    # Return 200 immediately; process in background so Fireflies doesn't timeout
    background_tasks.add_task(process_pipeline, payload.meetingId, company_target)
    return {"status": "accepted", "message": f"Processing started for {company_target}."}
