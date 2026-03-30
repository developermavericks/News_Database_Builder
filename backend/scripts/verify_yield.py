import os
import sys
# Add the backend directory to the path so we can import from db
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from db.database import get_db_sync, Article
from sqlalchemy import select, func

def check_yield():
    print("NEXUS: Performing High-Yield Content Validation...")
    try:
        with get_db_sync() as db:
            # Check for articles in the new 150-400 char bracket
            count = db.execute(
                select(func.count(Article.id))
                .where(func.length(Article.full_body).between(150, 400))
            ).scalar()
            
            # Check for total articles with full_body
            total = db.execute(
                select(func.count(Article.id))
                .where(Article.full_body != None)
            ).scalar()
            
            print(f"✅ Yield Summary:")
            print(f"   - Total Articles with Body: {total}")
            print(f"   - Articles in 150-400 char bracket: {count}")
            print(f"   - Coverage Improvement: {((count/total)*100 if total > 0 else 0):.1f}% of data would have been lost before tuning.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    check_yield()
