import os
import psycopg2
from dotenv import load_dotenv

# Load environment settings
load_dotenv()

def get_connection():
    """Get database connection."""
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), 
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )

def init_db():
    """Set up initial db tables."""
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        with open('db/schema.sql', 'r') as f:
            cur.execute(f.read())
        conn.commit()
        print("Tables created successfully!")
    except Exception as e:
        print(f"Error creating tables: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    init_db()