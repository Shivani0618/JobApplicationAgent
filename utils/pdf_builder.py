from fpdf import FPDF

class ResumePDFBuilder(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=15)
        self.add_page()
        # Set up a clean font
        self.set_font("Helvetica", size=11)

    def chapter_title(self, title):
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(44, 62, 80) # Dark Blue-Grey
        self.cell(0, 8, title, ln=True)
        # Draw underline
        self.set_draw_color(189, 195, 199)
        self.line(self.get_x(), self.get_y(), self.w - self.rmargin, self.get_y())
        self.ln(4)
        self.set_text_color(0, 0, 0) # Reset to black

    def add_contact_info(self, name, email, phone, links):
        self.set_font('Helvetica', 'B', 24)
        self.set_text_color(0, 0, 0)
        self.cell(0, 10, name, ln=True, align='C')
        
        self.set_font('Helvetica', '', 10)
        contacts = []
        if email: contacts.append(email)
        if phone: contacts.append(phone)
        for link in links:
            if link: contacts.append(link)
        
        contact_str = " | ".join(contacts)
        self.cell(0, 6, contact_str, ln=True, align='C')
        self.ln(6)

    def add_summary(self, summary_text):
        if not summary_text: return
        self.chapter_title("Professional Summary")
        self.set_font('Helvetica', '', 11)
        self.multi_cell(0, 5, summary_text)
        self.ln(6)

    def add_skills(self, skills_list):
        if not skills_list: return
        self.chapter_title("Skills")
        self.set_font('Helvetica', '', 11)
        if isinstance(skills_list, list):
            skills_str = ", ".join(skills_list)
        else:
            skills_str = str(skills_list)
        self.multi_cell(0, 5, skills_str)
        self.ln(6)

    def add_experience(self, work_history):
        if not work_history: return
        self.chapter_title("Work Experience")
        for job in work_history:
            self.set_font('Helvetica', 'B', 12)
            title = job.get('title', 'Position')
            company = job.get('company', 'Company')
            self.cell(0, 6, f"{title} at {company}", ln=True)
            
            self.set_font('Helvetica', 'I', 10)
            dates = job.get('dates', '')
            if dates:
                self.cell(0, 5, dates, ln=True)
            
            self.set_font('Helvetica', '', 11)
            desc = job.get('description', '')
            if desc:
                self.multi_cell(0, 5, desc)
            self.ln(4)
        self.ln(2)

    def add_projects(self, projects):
        if not projects: return
        self.chapter_title("Projects")
        for proj in projects:
            self.set_font('Helvetica', 'B', 12)
            title = proj.get('title', 'Project')
            self.cell(0, 6, title, ln=True)
            
            self.set_font('Helvetica', '', 11)
            desc = proj.get('description', '')
            if desc:
                self.multi_cell(0, 5, desc)
            self.ln(4)
        self.ln(2)

    def add_education(self, education):
        if not education: return
        self.chapter_title("Education")
        for edu in education:
            self.set_font('Helvetica', 'B', 12)
            degree = edu.get('degree', 'Degree')
            school = edu.get('school', 'Institution')
            self.cell(0, 6, f"{degree} - {school}", ln=True)
            
            self.set_font('Helvetica', 'I', 10)
            year = edu.get('year', '')
            if year:
                self.cell(0, 5, year, ln=True)
            self.ln(4)

def build_tailored_pdf(candidate_data, tailored_json, output_path):
    pdf = ResumePDFBuilder()
    
    # Build header area
    name = candidate_data.get('full_name', 'Candidate Name')
    email = candidate_data.get('email', '')
    phone = candidate_data.get('phone', '')
    links = [candidate_data.get('github_url', ''), candidate_data.get('portfolio_url', '')]
    pdf.add_contact_info(name, email, phone, links)
    
    # Render sections neatly
    pdf.add_summary(tailored_json.get('summary', ''))
    pdf.add_skills(tailored_json.get('skills', []))
    
    # Pull work entries
    work = candidate_data.get('work_history', [])
    if work:
        pdf.add_experience(work)
    
    # Add AI-tweaked projects
    pdf.add_projects(tailored_json.get('projects', []))
    
    # Add school details
    edu = candidate_data.get('education', [])
    if edu:
        pdf.add_education(edu)
        
    pdf.output(output_path)
