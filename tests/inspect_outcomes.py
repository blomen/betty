import pandas as pd
from sqlalchemy import create_engine
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from backend.src.db.models import DB_PATH

if not os.path.exists(DB_PATH):
    print("No DB")
    sys.exit(1)

engine = create_engine(f"sqlite:///{DB_PATH}")
query = """
SELECT provider_id, market, outcome, odds 
FROM odds 
WHERE provider_id='unibet' 
AND (market LIKE '%spread%' OR market LIKE '%total%')
LIMIT 20
"""
print(pd.read_sql(query, engine).to_string())
