
import base64
import requests

def resolve_gnews_url(url):
    try:
        # Step 1: Extract token
        token = url.split("/articles/")[1].split("?")[0]
        
        # Step 2: Google's 'new' (2024+) resolution endpoint 
        # This is an internal Google endpoint that sometimes returns the link in JSON
        # It's less protected than the browser redirect.
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        }
        # This is a known 'trick' URL to get the target
        resolve_url = f"https://news.google.com/rss/articles/{token}"
        
        # If we can't decode, we try a 'stealth' request to the mobile endpoint
        # Mobile endpoints often use simple headers for redirects.
        mobile_url = f"https://news.google.com/articles/{token}?hl=en-US&gl=US&ceid=US:en"
        
        with requests.Session() as s:
            s.headers.update(headers)
            r = s.get(mobile_url, allow_redirects=True, timeout=10)
            return r.url
    except Exception as e:
        return f"Error: {e}"

url = "https://news.google.com/rss/articles/CBMiakFVX3lxTE96SXR2cGI3dHBReDYxbUw5bWZlUnhSTHY0Z3ZoYXlYVGRId0ZUQ0hrdWtNQlRtQTVEVlNmME1CZ0QtNWN5SHNqb0F4eHUtbVExcG5wNWZoaUdDcUNsRlFWeklSREFDNEl0UGc?oc=5"
print(f"Testing Mobile-Endpoint Resolution:")
print(resolve_gnews_url(url))
