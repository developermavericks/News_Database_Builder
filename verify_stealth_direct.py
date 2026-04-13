
import asyncio
import sys
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def verify_stealth_direct():
    print("Initializing Direct Stealth Verification (No Proxy)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Apply Stealth
        await Stealth().apply_stealth_async(page)
        
        print("Navigating to SannySoft Bot Detection...")
        try:
            await page.goto("https://bot.sannysoft.com/", wait_until="networkidle", timeout=60000)
            content = await page.content()
            
            if "WebDriver (New)" in content:
                print("Checks started...")
                if 'class="failed"' in content:
                    print("WARNING: Some stealth checks failed in direct mode.")
                else:
                    print("SUCCESS: Stealth engine is 100% verified (Direct Mode).")
            
            await page.screenshot(path="stealth_direct.png")
            print("Screenshot saved to stealth_direct.png")

        except Exception as e:
            print(f"Direct Verification Error: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(verify_stealth_direct())
