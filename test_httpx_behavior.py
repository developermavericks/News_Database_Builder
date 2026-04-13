
import httpx

# Mock a 301 response
resp = httpx.Response(301, headers={'Location': 'https://google.com'}, content=b"Moved")
try:
    resp.raise_for_status()
    print("301 did NOT raise")
except Exception as e:
    print(f"301 raised: {type(e).__name__}: {e}")

# Mock a 404 response
resp = httpx.Response(404, request=httpx.Request("GET", "https://x.com"), content=b"Not Found")
try:
    resp.raise_for_status()
except Exception as e:
    print(f"404 raised: {type(e).__name__}: {e}")
