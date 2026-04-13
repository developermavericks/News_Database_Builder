
import httpx

def test():
    url = "https://news.google.com/rss/articles/CBMiakFVX3lxTE96SXR2cGI3dHBReDYxbUw5bWZlUnhSTHY0Z3ZoYXlYVGRId0ZUQ0hrdWtNQlRtQTVEVlNmME1CZ0QtNWN5SHNqb0F4eHUtbVExcG5wNWZoaUdDcUNsRlFWeklSREFDNEl0UGc?oc=5"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    }
    print(f"Testing direct GET with follow_redirects=True")
    try:
        # No proxy for this test to isolate the issue
        with httpx.Client(follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            print(f"Final Status: {resp.status_code}")
            print(f"Final URL: {resp.url}")
            print(f"History: {[r.status_code for r in resp.history]}")
    except Exception as e:
        print(f"Got Exception: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test()
