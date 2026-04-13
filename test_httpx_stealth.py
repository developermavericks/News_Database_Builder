
import httpx

def test():
    url = "https://news.google.com/rss/articles/CBMiakFVX3lxTE96SXR2cGI3dHBReDYxbUw5bWZlUnhSTHY0Z3ZoYXlYVGRId0ZUQ0hrdWtNQlRtQTVEVlNmME1CZ0QtNWN5SHNqb0F4eHUtbVExcG5wNWZoaUdDcUNsRlFWeklSREFDNEl0UGc?oc=5"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://news.google.com/",
        "sec-ch-ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    print(f"Testing direct GET with ENRICHED headers")
    try:
        with httpx.Client(follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            print(f"Final Status: {resp.status_code}")
            print(f"Final URL: {resp.url}")
            print(f"History: {[r.status_code for r in resp.history]}")
            if "google.com" not in str(resp.url):
                 print("SUCCESS: Target site reached.")
            else:
                 print("STILL on Google.")
    except Exception as e:
        print(f"Got Exception: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test()
