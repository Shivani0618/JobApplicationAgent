import asyncio
import json
from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph, END
from db.db_manager import get_connection
from form_filler import SmartFiller
from browser.automation import JobBrowser
import os

# App Data State
class AgentState(TypedDict):
    job_id:             Optional[int]
    job_url:            str
    company_name:       str
    ats_type:           str
    candidate_data:     dict
    custom_answers:     dict
    unanswered_fields:  list
    fail_reason:        str
    application_status: str  # pending → running → submitted / insufficient_knowledge / failed / backlog

# Grab next job
def fetch_job_data(state: AgentState):
    print("\n Node: fetch_job")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, job_url, company_name, ats_type
        FROM jobs
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 1
    """)
    job = cur.fetchone()
    conn.close()

    if not job:
        print("Queue is empty.")
        return {"application_status": "empty_queue"}

    filler = SmartFiller()
    candidate_data, custom_answers = filler.fetch_all_candidate_context()

    print(f"Picked up job: {job[2]} | {job[1]}")
    return {
        "job_id":             job[0],
        "job_url":            job[1],
        "company_name":       job[2] or "",
        "ats_type":           job[3] or "Unknown",
        "candidate_data":     candidate_data,
        "custom_answers":     custom_answers,
        "unanswered_fields":  [],
        "fail_reason":        "",
        "application_status": "running",
    }

# Lock job
def mark_running(state: AgentState):
    if not state.get("job_id"):
        return state
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (state["job_id"],))
    conn.commit()
    cur.close()
    conn.close()
    return state

# Automate browser flow
async def run_automation(state: AgentState):
    print(f"\n Node: run_automation")
    print(f"Target: {state['job_url']}")

    if not state.get("job_id"):
        return {**state, "application_status": "failed", "fail_reason": "No job_id in state."}

    bot = JobBrowser()
    filler = SmartFiller()

    unanswered_labels = []
    fail_reason = ""
    status = "failed"

    try:
        await bot.start(headless=False)
        await asyncio.sleep(2)

        # Look up job site
        ats_type = await bot.detect_ats(state["job_url"])
        print(f"Detected ATS: {ats_type}")

        # Ensure job is open
        is_open, reason = await bot.check_job_is_open()
        if not is_open:
            print(f"Job is closed: {reason}")
            return {**state, "application_status": "failed", "fail_reason": reason}

        # Learn about the role 
        print("Scraping job description...")
        job_description = await bot.scrape_job_description()
        job_context = f"Applying to {state['company_name']}. Role context: {job_description[:2000]}"

        # Prep resume and letter 
        static_resume_path = os.path.abspath("demo/ShivaniResume.pdf")
        os.makedirs("temp", exist_ok=True)
        cover_letter_path = os.path.abspath("temp/cover_letter.txt")
        
        fallback_cover_letter = (
            f"Dear Hiring Team at {state.get('company_name', 'your company')},\n\n"
            f"I am excited to apply for this role. Please find my resume attached.\n\n"
            f"Best regards,\n{state['candidate_data'].get('full_name', 'Applicant')}"
        )
        
        # Write personalized letter safely
        cover_letter_content = fallback_cover_letter
        try:
            print("Attempting to generate personalized cover letter with Gemini...")
            job_title = "the open position" 
            generated_cl = filler.llm.generate_cover_letter(
                state['candidate_data'].get('full_name', 'Applicant'),
                job_title,
                state.get('company_name', 'your company'),
                job_context
            )
            
            # Verify letter generated OK
            if generated_cl and "An error occurred" not in generated_cl:
                print(" Successfully generated tailored cover letter.")
                cover_letter_content = generated_cl
            else:
                print(" Gemini returned an error. Using static cover letter.")
        except Exception as e:
            print(f" API Rate Limit hit or Generation failed ({e}). Falling back to static cover letter.")
            
        with open(cover_letter_path, "w") as f:
            f.write(cover_letter_content)

        # Edit resume safely
        final_resume_path = static_resume_path
        try:
            print("Attempting to tailor resume and construct PDF...")
            from utils.llm_handler import JobAgentLLM
            from utils.pdf_builder import build_tailored_pdf
            
            # Extract basic info
            base_resume_text = JobAgentLLM.extract_text_from_pdf(static_resume_path)
            tailored_json = filler.llm.tailor_resume(base_resume_text, job_context)
            
            if tailored_json and isinstance(tailored_json, dict) and 'summary' in tailored_json:
                generated_pdf_path = os.path.abspath("temp/tailored_resume.pdf")
                build_tailored_pdf(state['candidate_data'], tailored_json, generated_pdf_path)
                print(" Successfully tailored and built resume PDF format.")
                final_resume_path = generated_pdf_path
            else:
                print(" Gemini JSON structure issue. Using static resume.")
        except Exception as e:
            print(f" API Rate Limit hit or Resume generation failed ({e}). Falling back to static resume.")

        # Save docs for later
        state["custom_answers"]["_resume_path"] = final_resume_path
        state["custom_answers"]["_cover_letter_path"] = cover_letter_path

        # Hit apply 
        print("Clicking apply button...")
        clicked = await bot.click_apply_button()
        if not clicked:
            return {**state, "application_status": "failed", "fail_reason": "Could not click apply button."}

        await asyncio.sleep(2)

        # Go through form steps
        # Supports up to 10 steps 
        MAX_STEPS = 10
        hitl_budget = 3  

        for step in range(MAX_STEPS):
            print(f"\n  ── Form step {step + 1} ──")

            # Scan questions on page
            fields = await bot.get_form_fields(ats_type=ats_type)
            print(f"  Found {len(fields)} fields.")

            if fields:
                # Process answers grouped
                filled_pairs, unknowns = await filler.resolve_all_fields(
                    fields, state["candidate_data"], state["custom_answers"], job_context
                )

                # Let's type
                await filler.fill_all_fields(bot, filled_pairs)

                # Ask user for unknown items
                if unknowns and hitl_budget > 0:
                    hitl_budget -= 1
                    hitl_answers = await filler.batch_hitl(unknowns, state["custom_answers"], timeout=120)

                    if hitl_answers:
                        # Apply fresh answers
                        hitl_pairs = []
                        for field in unknowns:
                            if field['label'] in hitl_answers:
                                hitl_pairs.append((field, hitl_answers[field['label']]))
                            else:
                                unanswered_labels.append(field['label'])
                        await filler.fill_all_fields(bot, hitl_pairs)
                    else:
                        # Mark unanswered on timeout
                        unanswered_labels.extend([f['label'] for f in unknowns])
                        print("  HITL timeout: marking remaining fields as unanswered.")

                elif unknowns:
                    # User asked too many times
                    unanswered_labels.extend([f['label'] for f in unknowns])
                    print("  HITL budget exhausted. Logging unanswered fields.")

            await asyncio.sleep(1)  # tiny breath

            # Check where to go next
            action = await bot.click_next_or_submit()
            print(f"  Button action: {action}")

            if action == 'submitted':
                status = "submitted"
                print("\n Application submitted successfully!")
                await bot.dismiss_modal_if_open()
                break
            elif action == 'review':
                print("  On review step — will check for Submit next iteration.")
                continue
            elif action == 'next':
                continue
            elif action == 'stuck':
                print("  No forward button found. Form may be complete or stuck.")
                # Form might need more visual interaction
                status = "insufficient_knowledge" if unanswered_labels else "failed"
                fail_reason = "No submit/next button found after filling fields."
                break

        # Submission checking logic
        if status == "failed" and not fail_reason:
            fail_reason = "Reached max steps without submission."
            status = "insufficient_knowledge" if unanswered_labels else "failed"

    except Exception as e:
        print(f"\n Automation error: {e}")
        status = "failed"
        fail_reason = str(e)
    finally:
        await bot.shutdown()

    # Set run outcome
    # Mark skipped things
    if status == "submitted" and unanswered_labels:
        fail_reason = f"Submitted with {len(unanswered_labels)} unanswered field(s)."

    # Note if things were left empty
    if status != "submitted" and unanswered_labels and status != "failed":
        status = "insufficient_knowledge"

    return {
        **state,
        "ats_type":            ats_type if 'ats_type' in dir() else state.get("ats_type", "Unknown"),
        "unanswered_fields":   unanswered_labels,
        "fail_reason":         fail_reason,
        "application_status":  status,
    }

# Record to DB
def record_result(state: AgentState):
    print(f"\n Node: record_result")
    if not state.get("job_id"):
        return {**state, "application_status": "done"}

    final_status = state["application_status"]

    # Double check final status is safe
    valid_final = {"submitted", "failed", "insufficient_knowledge", "backlog"}
    if final_status not in valid_final:
        final_status = "failed"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE jobs
        SET status = %s,
            failure_reason = %s,
            unanswered_fields = %s
        WHERE id = %s
    """, (
        final_status,
        state.get("fail_reason", ""),
        json.dumps(state.get("unanswered_fields", [])),
        state["job_id"]
    ))
    conn.commit()
    cur.close()
    conn.close()

    print(f"DB updated → job {state['job_id']} : {final_status}")
    if state.get("unanswered_fields"):
        print(f"Unanswered fields logged: {state['unanswered_fields']}")

    return {**state, "application_status": "done"}

# Workflow rules
def route_after_fetch(state: AgentState):
    if state["application_status"] == "empty_queue":
        return "empty"
    return "process"

# Configure app sequence
workflow = StateGraph(AgentState)

workflow.add_node("fetcher",    fetch_job_data)
workflow.add_node("marker",     mark_running)
workflow.add_node("automator",  run_automation)
workflow.add_node("recorder",   record_result)

workflow.set_entry_point("fetcher")

workflow.add_conditional_edges(
    "fetcher",
    route_after_fetch,
    {"process": "marker", "empty": END}
)

workflow.add_edge("marker",    "automator")
workflow.add_edge("automator", "recorder")
workflow.add_edge("recorder",  END)

app = workflow.compile()
