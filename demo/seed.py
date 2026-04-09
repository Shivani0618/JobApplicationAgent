import os
import psycopg2
import json
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), 
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )

def seed_db():
    print("Connecting to DB to seed data...")
    conn = get_connection()
    cur = conn.cursor()

    # Set up fresh tables
    with open('db/schema.sql', 'r') as f:
        cur.execute(f.read())

    # Add dummy candidate profile
    work_history = [
        {
            "company": "InternPE",
            "title": "AIML Intern",
            "start_date": "2025-05-01",
            "end_date": "2025-06-30",
            "description": "Architected a predictive fatigue monitoring system utilizing Python 3.12, TensorFlow, and OpenCV, achieving 99.7 detection accuracy at a low-latency processing rate of 15-25 FPS."
        }
    ]
    education = [
        {
            "institution": "Sharda University",
            "degree": "Bachelor of Technology in Computer Science and Engineering",
            "graduation_year": 2027
        }
    ]
    skills = ["Python", "PostgreSQL", "JAVA", "Docker", "FastAPI", "Git", "GitHub"]
    
    cur.execute("""
        INSERT INTO candidates (full_name, email, phone, resume_path, portfolio_url, github_url, work_history, education, skills)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, ("Shivani Sharma", "manrek.shivani8@gmail.com", "+918439952593", "demo/ShivaniResume.pdf", None, "https://github.com/Shivani0618", json.dumps(work_history), json.dumps(education), json.dumps(skills)))

    # Add common answers
    answers = {
        "require sponsorship": "No",
        "willing to relocate": "No",
        "salary expectation": "20,00,000",
        "how did you hear about this job": "LinkedIn",
        "notice period": "30 days",
        "veteran status": "I am not a protected veteran",
        "disability status": "No, I do not have a disability",
        "gender": "Female",
        "race/ethnicity": "Asian",
        "highest level of education": "Bachelor's Degree",
        "years of experience": "3"
    }

    for q, a in answers.items():
        cur.execute("INSERT INTO custom_answers (question_key, answer_text) VALUES (%s, %s)", (q, a))

    # Add some demo jobs
    demo_jobs = [
        {"url": "https://in.indeed.com/viewjob?jk=f7fcd484e6bbd633&from=shareddesktop_copy", "company": "Datafoundry", "ats": "Indeed"},
        {"url": "https://unstop.com/o/7gjco4y?lb=uV2mPBQ2&utm_medium=Share&utm_source=internships&utm_campaign=Shivasha1169", "company": "Rhosigma Technologies Pvt. Ltd.", "ats": "Unstop"},
        {"url": "https://www.linkedin.com/jobs/view/4389858122", "company": " ", "ats": "LinkedIn"},
    ]

    for job in demo_jobs:
        cur.execute("""
            INSERT INTO jobs (job_url, company_name, ats_type, status)
            VALUES (%s, %s, %s, 'pending')
        """, (job["url"], job["company"], job["ats"]))

    conn.commit()
    cur.close()
    conn.close()
    print("Database seeded successfully with demo candidate 'Shivani' and  jobs!")

if __name__ == "__main__":
    seed_db()
