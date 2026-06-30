import os
import time
import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from google import genai
from google.genai import types

# ── Clients ───────────────────────────────────────────────────────────────
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
FIREFLIES_API_KEY = os.getenv("FIREFLIES_API_KEY")

app = FastAPI()

# ── Models ────────────────────────────────────────────────────────────────
class WebhookPayload(BaseModel):
    eventType: str
    meetingId: str
    clientReferenceId: str | None = None

# ── Helpers ───────────────────────────────────────────────────────────────
def get_fireflies_transcript_and_title(meeting_id: str):
    query = """
    query Transcript($id: String!) {
      transcript(id: $id) {
        title
        sentences {
          text
          speaker_name
        }
      }
    }
    """
    try:
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

        if "errors" in data:
            print(f"⚠️ [FIREFLIES] GraphQL errors: {data['errors']}")
            return "", ""

        transcript_data = data.get("data", {}).get("transcript", {})
        title = transcript_data.get("title", "Unknown Meeting")
        sentences = transcript_data.get("sentences", [])

        if not sentences:
            return "", title

        transcript_text = "\n".join(
            f"{s.get('speaker_name', 'Unknown')}: {s.get('text', '')}"
            for s in sentences
        )
        return transcript_text, title
    except Exception as e:
        print(f"❌ [FIREFLIES] Unexpected error: {e}")
        return "", ""

# ── AI Scoring ────────────────────────────────────────────────────────────
def analyze_and_score_with_gemini(fireflies_transcript: str) -> str | None:
    print("\n🧠 [AI CORE ENGINE] Scoring transcript...")

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

DATA SOURCE: Meeting Transcript
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
            float(raw)  # validate it's numeric
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


# ── Webhook ───────────────────────────────────────────────────────────────
@app.post("/webhooks/fireflies")
async def handle_webhook(payload: WebhookPayload):
    print(f"\n🚀 [PIPELINE] Processing meeting: {payload.meetingId} | Event: {payload.eventType}")

    transcript, title = get_fireflies_transcript_and_title(payload.meetingId)
    if not transcript:
        return {"status": "error", "message": "Empty or missing transcript."}

    score = analyze_and_score_with_gemini(transcript)
    if score is None:
        return {"status": "error", "message": "Scoring failed."}

    print(f"💾 [SCORE RESULT] {title} → Score: {score}")
    send_slack_notification(title, score, payload.meetingId)

    return {
        "status": "success",
        "meetingId": payload.meetingId,
        "title": title,
        "score": score,
    }
