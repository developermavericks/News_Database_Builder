
import httpx

def test_direct_resolution():
    url = "https://news.google.com/rss/articles/CBMiakFVX3lxTE96SXR2cGI3dHBReDYxbUw5bWZlUnhSTHY0Z3ZoYXlYVGRId0ZUQ0hrdWtNQlRtQTVEVlNmME1CZ0QtNWN5SHNqb0F4eHUtbVExcG5wNWZoaUdDcUNsRlFWeklSREFDNEl0UGc?oc=5"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    }
    print(f"Testing direct GET (No Proxy) for resolution:")
    try:
        with httpx.Client(follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            print(f"Status: {resp.status_code}")
            print(f"Final URL: {resp.url}")
            if "google.com" not in str(resp.url):
                print("SUCCESS: Direct resolution escapes the Google wall.")
            else:
                print("STILL on Google (Redirection jailed).")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_direct_resolution()
