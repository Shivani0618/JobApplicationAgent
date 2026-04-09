DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS custom_answers;
DROP TABLE IF EXISTS candidates;

CREATE TABLE candidates (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(255),
    email VARCHAR(255),
    phone VARCHAR(20),
    resume_path TEXT, -- Local path to your PDF resume
    portfolio_url TEXT,
    github_url TEXT,
    work_history JSONB, -- Stores roles/dates as JSON
    education JSONB,
    skills JSONB
);

CREATE TABLE custom_answers (
    id SERIAL PRIMARY KEY,
    question_key VARCHAR(255) UNIQUE, 
    answer_text TEXT                  
);

CREATE TABLE jobs (
    id SERIAL PRIMARY KEY,
    job_url TEXT UNIQUE,
    company_name VARCHAR(255),
    ats_type VARCHAR(50), 
    status VARCHAR(50) DEFAULT 'pending', 
    retry_count INT DEFAULT 0,
    failure_reason TEXT,
    unanswered_fields JSONB,
    logs TEXT, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);