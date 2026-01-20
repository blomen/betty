import asyncio
import sys
import os
import logging

# Add project root to path
sys.path.append(os.getcwd())

# Setup Logging
logging.basicConfig(level=logging.INFO)

from backend.src.factory import ExtractorFactory

async def main():
    print("Initializing Factory...")
    factory = ExtractorFactory.get_instance()
    
    # We expect Unibet to now be using the Experimental KambiRetriever
    print("Getting Unibet Extractor...")
    try:
        extractor = factory.get_extractor("unibet")
        print(f"Successfully loaded extractor: {extractor}")
        print(f"Type: {type(extractor)}")
    except Exception as e:
        print(f"Failed to load extractor: {e}")
        return

    print("Extracting Football (football)...")
    try:
        events = await extractor.extract("football", limit=5)
        print(f"Extraction Complete. Found {len(events)} events.")
        for ev in events:
            print(f" - {ev.name} ({ev.start_time})")
            print(f"   Markets: {[m['type'] for m in ev.markets]}")
    except Exception as e:
        print(f"Extraction failed: {e}")
    finally:
        await extractor.close()

if __name__ == "__main__":
    asyncio.run(main())
