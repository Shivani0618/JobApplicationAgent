import asyncio
import sys
import re
from utils.llm_handler import JobAgentLLM
from db.db_manager import get_connection

# Map form labels to standard field names
FIELD_ALIASES = {
    "first_name":    ["first name", "firstname", "given name", "your first name"],
    "last_name":     ["last name", "lastname", "surname", "family name", "your last name"],
    "full_name":     ["full name", "name", "your name", "legal name"],
    "email":         ["email", "email address", "e-mail", "your email"],
    "phone":         ["phone", "phone number", "mobile", "mobile number", "contact number"],
    "linkedin":      ["linkedin", "linkedin profile", "linkedin url", "linkedin profile url"],
    "github":        ["github", "github url", "github profile"],
    "portfolio":     ["portfolio", "portfolio url", "website", "personal website"],
    "location":      ["location", "city", "current location", "city/state", "city, state"],
    "experience_years": ["years of experience", "total experience", "years experience", "how many years"],
    "education":     ["highest level of education", "education level", "degree", "qualification"],
    "skills":        ["skills", "technical skills", "key skills"],
    "sponsorship":   ["require sponsorship", "visa sponsorship", "work authorization",
                      "do you need sponsorship", "sponsorship required"],
    "relocate":      ["willing to relocate", "open to relocation", "relocation"],
    "salary":        ["salary expectation", "expected salary", "desired salary",
                      "expected ctc", "desired ctc", "salary"],
    "notice_period": ["notice period", "when can you start", "availability", "start date"],
    "veteran":       ["veteran status", "are you a veteran", "protected veteran"],
    "disability":    ["disability status", "do you have a disability", "disability"],
    "gender":        ["gender", "sex"],
    "race":          ["race", "ethnicity", "race/ethnicity"],
    "referral":      ["how did you hear", "referral source", "how did you find", "source"],
    "cover_letter":  ["cover letter", "why do you want", "motivation letter"],
    "resume":        ["resume", "cv", "upload resume", "attach resume", "upload cv"],
}

def normalize_label(label: str) -> str:
    """Standardize form labels into plain names."""
    clean = label.lower().strip()
    # Clean up symbols
    clean = re.sub(r'[*\(\)]+', '', clean).strip()
    for canonical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in clean or clean in alias:
                return canonical
    return clean


class SmartFiller:
    def __init__(self):
        self.llm = JobAgentLLM()

    def fetch_all_candidate_context(self):
        """Get candidate info from the database."""
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT full_name, email, phone, resume_path,
                   portfolio_url, github_url, work_history, education, skills
            FROM candidates LIMIT 1
        """)
        row = cur.fetchone()

        cur.execute("SELECT question_key, answer_text FROM custom_answers")
        answers = cur.fetchall()
        conn.close()

        if not row:
            return {}, {}

        full_name = row[0] or ""
        name_parts = full_name.strip().split(" ", 1)

        candidate_data = {
            "full_name":    full_name,
            "first_name":   name_parts[0] if name_parts else "",
            "last_name":    name_parts[1] if len(name_parts) > 1 else "",
            "email":        row[1] or "",
            "phone":        row[2] or "",
            "resume_path":  row[3] or "",
            "portfolio_url":row[4] or "",
            "github_url":   row[5] or "",
            "work_history": row[6] or [],
            "education":    row[7] or [],
            "skills":       row[8] or [],
        }

        custom_answers = {k.lower(): v for k, v in answers}
        return candidate_data, custom_answers

    def _save_new_custom_answer(self, key: str, value: str):
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO custom_answers (question_key, answer_text)
                VALUES (%s, %s)
                ON CONFLICT (question_key) DO UPDATE SET answer_text = EXCLUDED.answer_text
            """, (key.lower(), value))
            conn.commit()
        except Exception as e:
            print(f"  Warning: Could not save custom answer: {e}")
        finally:
            cur.close()
            conn.close()

    def resolve_from_profile(self, canonical_key: str, candidate_data: dict, custom_answers: dict):
        """Try filling fields using just the database first."""
        # Check standard profile info
        if canonical_key in candidate_data and candidate_data[canonical_key]:
            val = candidate_data[canonical_key]
            # Make lists readable
            if isinstance(val, list):
                if canonical_key == "skills":
                    return ", ".join(val) if val else None
                return str(val[0]) if val else None
            return str(val)

        # Check predefined custom answers
        if canonical_key in custom_answers and custom_answers[canonical_key]:
            return str(custom_answers[canonical_key])

        # Fallback to exact user input
        return None

    async def resolve_field(self, field: dict, candidate_data: dict, custom_answers: dict, job_context=""):
        """Find the right answer by checking DB, then custom list, then AI."""
        label = field.get('label', '')
        field_type = field.get('type', 'text')
        canonical = normalize_label(label)

        # Handle files
        if field_type == 'file' or canonical == 'resume':
            path = candidate_data.get('resume_path') or custom_answers.get('_resume_path')
            return path, 'db'

        if canonical == 'cover_letter':
            path = custom_answers.get('_cover_letter_path')
            return path, 'custom'

        # Try database lookups
        db_val = self.resolve_from_profile(canonical, candidate_data, custom_answers)
        if db_val:
            return db_val, 'db'

        # Double check custom list
        label_lower = label.lower()
        for key, val in custom_answers.items():
            if key in label_lower or label_lower in key:
                return val, 'custom'

        # Ask AI if we still don't know
        # Tell AI what dropdown options are available
        options_hint = ""
        if field.get('options'):
            option_texts = [o['text'] for o in field['options']]
            options_hint = f"Available options: {', '.join(option_texts)}"

        llm_val = self.llm.infer_form_field(
            label,
            candidate_data,
            custom_answers,
            f"{job_context}\n{options_hint}"
        )

        if llm_val and llm_val.strip() not in ["UNABLE_TO_INFER", "", "N/A"]:
            return llm_val.strip(), 'llm'

        return None, None

    async def resolve_all_fields(self, fields: list, candidate_data: dict, custom_answers: dict, job_context=""):
        """Try to answer every field on the form."""
        filled = []
        unanswered = []

        for field in fields:
            label = field.get('label', '')
            if not label or label.lower() in ['unknown_field', '']:
                continue

            value, source = await self.resolve_field(field, candidate_data, custom_answers, job_context)

            if value:
                print(f"  [{source.upper()}] '{label}' → '{str(value)[:50]}'")
                filled.append((field, value))
            else:
                print(f"  [UNKNOWN] '{label}' → needs HITL")
                unanswered.append(field)

        return filled, unanswered

    async def batch_hitl(self, unanswered_fields: list, custom_answers: dict, timeout=30):
        """Ask the human for help if we get stuck."""
        if not unanswered_fields:
            return {}

        print("\n" + "="*55)
        print("HUMAN INPUT REQUIRED!!")
        print("="*55)
        print("The agent could not answer the following fields:")
        for i, field in enumerate(unanswered_fields, 1):
            print(f"  {i}. {field['label']}")
        print(f"\nYou have {timeout} seconds to answer each.")
        print("Press Enter without typing to skip a field.\n")

        answers = {}
        loop = asyncio.get_running_loop()

        for field in unanswered_fields:
            label = field['label']
            print(f"→ {label}: ", end='', flush=True)
            try:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(None, sys.stdin.readline),
                    timeout=float(timeout)
                )
                val = raw.strip()
                if val:
                    answers[label] = val
                    custom_answers[label.lower()] = val
                    self._save_new_custom_answer(label.lower(), val)
                    print(f" Saved '{label}' = '{val}'")
                else:
                    print(f" Skipped '{label}'")
            except asyncio.TimeoutError:
                print(f"\n Timeout on '{label}'. Moving on.")
                break  

        print("="*55 + "\n")
        return answers

    async def fill_all_fields(self, browser, fields_with_values: list):
        """Type recognized answers into the browser."""
        success = 0
        for field, value in fields_with_values:
            ok = await browser.fill_field(field, value)
            if ok:
                success += 1
            await asyncio.sleep(0.3)  # Small pause 
        return success
