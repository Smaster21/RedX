import asyncio
import re
from langchain_community.tools import DuckDuckGoSearchRun

async def test():
    user_input = "analyze https://github.com/transilienceai/communitytools.git and tell me its skills"
    url_match = re.search(r"https?://[^\s]+", user_input)
    search_tool = DuckDuckGoSearchRun()
    
    if url_match:
        target_url = url_match.group(0)
        print(f"URL matched: {target_url}")
        try:
            # This is what proxy.py does:
            search_results = await asyncio.to_thread(search_tool.run, f"site:{target_url} full content")
            print("--- Search Results (URL Path) ---")
            print(search_results)
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("No URL matched.")

asyncio.run(test())
