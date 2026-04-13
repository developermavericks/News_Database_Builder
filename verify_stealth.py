
import asyncio
import sys
import os

# Ensure backend is in path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from scraper.browser import browser_pool

async def verify_stealth():
    print("Initializing Stealth Verification...")
    try:
        async with browser_pool.acquire_page() as page:
            print("Navigating to SannySoft Bot Detection...")
            await page.goto("https://bot.sannysoft.com/", wait_until="networkidle")
            
            # Simple check for 'webdriver' being present in the results
            content = await page.content()
            if "WebDriver (New)" in content:
                print("Found SannySoft results. Checking for 'failed' indicator...")
                # SannySoft uses 'failed' class for detected properties
                if 'class="failed"' in content:
                    print("WARNING: Some stealth checks failed.")
                else:
                    print("SUCCESS: All JS-level stealth checks passed.")
            
            # Save a screenshot for manual review
            await page.screenshot(path="stealth_verification.png")
            print("Screenshot saved to stealth_verification.png")

    except Exception as e:
        print(f"Verification Error: {e}")
    finally:
        await browser_pool.close()

if __name__ == "__main__":
    asyncio.run(verify_stealth())
