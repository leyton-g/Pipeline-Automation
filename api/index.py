import os
import time
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# FastAPI initialization that Vercel looks for
app = FastAPI()

class WebhookPayload(BaseModel):
    eventType: str
    meetingId: str
    title: str

def analyze_and_score_with_gemini(airtable_notes: str, fireflies_transcript: str, scoring_reference: str):
    print("\n🧠 [AI CORE ENGINE] Dissecting deal streams with Low-Inflation Calibration...")
    
    system_instruction = (
        "You are an elite, highly skeptical venture capital investment analyst at Tech Square Ventures. "
        "Your task is to mathematically score opportunities on a strict 0 to 10 scale. "
        "Venture capital grading is inherently stringent. A score of 10 represents absolute perfection with zero risks. "
        "A score of 7-8 represents a strong, standard institutional-grade deal. Avoid grade inflation."
    )
    
    prompt_body = f"""
    Evaluate this opportunity strictly using a 0 to 10 scale based on the criteria weights provided below.

    [GOLD STANDARD SCORING ANCHOR]
    Use this real historical evaluation benchmark to calibrate your scoring rigor:
    - Example Evaluation: A startup with elite founders (MIT/Harvard/McKinsey), $140B TAM framing, and 3 enterprise pilots converting next month was rigorously graded a 7.78 to 7.83 Weighted Composite. 
    - Alignment was scored a 6.5 because a competing-lead dynamic adds term friction.
    - Team was scored a 7.5 because despite elite degrees, there is no prior disclosed exit history.
    - Region was scored a 7.0 because the exact HQ location was not explicitly verified.
    Apply this exact level of critical skepticism and deduction to the data below.

    DATA SOURCE 1: Official Airtable Scoring Rules Matrix
    \"\"\"{scoring_reference}\"\"\"
    
    DATA SOURCE 2: Historical CRM Notes
    \"\"\"{airtable_notes}\"\"\"
    
    DATA SOURCE 3: Live Meeting Transcript
    \"\"\"{fireflies_transcript}\"\"\"
    
    CRITERIA TO EVALUATE & WEIGHTS TO APPLY:
    1. Alignment (20% Weight) | 2. Market (20% Weight) | 3. Team (25% Weight) | 4. Business Model (10% Weight) | 5. Sector (10% Weight) | 6. Region (15% Weight)

    OUTPUT FORMAT RULES:
    - In your background thinking context, calculate the mathematical sum of all (Score * Weight) variables.
    - Round the final result to exactly two decimal places.
    - Output ONLY that final calculated single number (e.g., 7.78).
    - CRITICAL: Do NOT output a markdown table, do NOT add introductory or concluding sentences, and do NOT use markdown formatting. Just return the plain numeric value.
    """
    
    max_retries = 3
    delay = 2  
    
    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt_body,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                )
            )
            return f"**Score: {response.text.strip()} / 10**"
        except Exception as e:
            if "503" in str(e) and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2  
            else:
                print(f"❌ [API ERROR] Gemini processing failed completely: {e}")
                return None

def process_pipeline(meeting_id: str, company_target: str) -> str:
    scoring_rules = get_live_airtable_scoring_reference()
    
    # Clean string extraction formula that bypassed the 422 error
    formula = "FIND('" + company_target + "', {Name})"
    
    records = company_table.all(formula=formula)
    
    if not records:
        print(f"⚠️ [NOT FOUND] No row found for {company_target}")
        return None
        
    record = records[0]
    record_id = record['id']
    historical_notes = record['fields'].get('Notes', '')
    
    live_transcript = get_fireflies_transcript(meeting_id)
    if not live_transcript or live_transcript.strip() == "":
        return "Empty transcript payload context."
        
    scorecard_matrix = analyze_and_score_with_gemini(historical_notes, live_transcript, scoring_rules)
    
    if scorecard_matrix and record_id:
        print(f"💾 [AIRTABLE WRITE] Pushing generated matrix directly to David's 'Score' cell...")
        company_table.update(record_id, {"Score": scorecard_matrix})
        return scorecard_matrix
        
    return "Pipeline finished."

@app.post("/webhooks/fireflies")
async def handle_webhook(payload: WebhookPayload, x_tsv_token: str = Header(None)):
    # Validate handshake
    if x_tsv_token != os.getenv("TSV_WEBHOOK_SECRET", "my-secret-handshake"):
        raise HTTPException(status_code=401, detail="Invalid handshake token")
        
    # Extract clean target company string out of full name title string
    company_target = payload.title.split(" x ")[0].strip()
    
    result = process_pipeline(payload.meetingId, company_target)
    return {"status": "accepted", "message": f"Successfully compiled and saved matrix scorecard for {company_target}."}
