import os
from google import genai
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv()

class JobAgentLLM:
    def __init__(self):
        # Configure Gemini API
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model_id = "gemini-2.5-flash"

    def extract_text_from_pdf(pdf_path):
        try:
            reader = PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text()
            return text
        except Exception as e:
            print(f"Error reading PDF: {e}")
            return ""
    
    def tailor_resume(self, base_resume_text, job_description):
        """Ask AI to tweak the resume for the role."""
        import json
        prompt = f"""
        I have a base resume and a job description. 
        Rewrite the 'Professional Summary', 'Skills', and 'Projects' sections to align with the job requirements while staying truthful. 
        Do not change the fundamental project descriptions, just add relevant keywords from the job description.
        
        Base Resume: {base_resume_text}
        Job Description: {job_description}
        
        You MUST return ONLY a raw JSON object (with no markdown decorators like ```json) with the following exact keys:
        {{
            "summary": "A single string of the tailored professional summary.",
            "skills": ["A list", "of", "tailored", "skills, keeping them concise."],
            "projects": [
                {{
                     "title": "Project Name",
                     "description": "Tailored project description text here."
                }}
            ]
        }}
        """
        try:
            response = self.client.models.generate_content(
                model=self.model_id, 
                contents=prompt
            )
            raw = response.text.strip()
            # Drop weird code block wrap formatting seen sometimes
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            return json.loads(raw.strip())
        except Exception as e:
            print(f"Error structurally generating resume: {e}")
            return None

    def generate_cover_letter(self, candidate_name, job_title, company, job_description):
        """Write out a friendly short cover letter."""
        prompt = f"""
        Write a professional, concise cover letter for {candidate_name} 
        applying for the {job_title} role at {company}.
        Use the following Job Description for context: {job_description}
        Keep it under 300 words and maintain a confident, helpful tone.
        """
        try:
            response = self.client.models.generate_content(
                model=self.model_id, 
                contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"Error generating cover letter: {e}")
            return "An error occurred while generating the cover letter. Please check the model configuration."

    def infer_form_field(self, field_label, candidate_data, custom_answers, job_description=""):
        """Use guessing logic to answer neatly."""
        prompt = f"""
        You are an AI assistant helping a candidate fill out a job application.
        The application is asking for the field: "{field_label}".
        
        Here is the candidate's profile data: {candidate_data}
        Here are custom answers they provided before: {custom_answers}
        Here is the job description (if applicable): {job_description}
        
        Answer the field accurately and concisely. Return JUST the final string/text intended for the UI element.
        Do not add quotes or padding logic.
        If it's a yes/no question, return Yes or No.
        If the question is highly sensitive, requires legal documentation, or cannot be confidently inferred from the provided candidate profile, return exactly: "UNABLE_TO_INFER".
        """
        try:
            response = self.client.models.generate_content(
                model=self.model_id, 
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            print(f"Error inferring form field '{field_label}': {e}")
            return "UNABLE_TO_INFER"

if __name__ == "__main__":
    agent = JobAgentLLM()
    print("Testing LLM Connection...")
    try:
        test_cl = agent.generate_cover_letter("Shivani", "Software Engineer", "Google", "Looking for Python experts.")
        print("-" * 30)
        print(test_cl)
    except Exception as e:
        print(f"Error during test: {e}")