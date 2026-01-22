
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever

def test_parsing():
    config = {"id": "snabbare"}
    r = SnabbareRetriever(config)
    
    test_cases = [
        ("13:30", "Today at 13:30"),
        ("Idag 18:00", "Today at 18:00"),
        ("Imorgon 16:00", "Tomorrow at 16:00"),
        ("Lör 24 Jan. 13:30", "Jan 24 at 13:30"),
        ("24 Jan. 13:30", "Jan 24 at 13:30"),
        ("Ons 21 Maj 20:45", "May 21 at 20:45"),
    ]
    
    print(f"Current time: {datetime.now()}")
    for ts, desc in test_cases:
        parsed = r._parse_time(ts)
        print(f"Input: {ts:20} | Desc: {desc:20} | Parsed: {parsed}")

if __name__ == "__main__":
    test_parsing()
