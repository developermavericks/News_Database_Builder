import httpx
import sys

def test_proxy(port):
    creds = 'jxgqvosn:symou02ck2bw'
    host = 'p.webshare.io'
    proxy_url = f"http://{creds}@{host}:{port}"
    print(f"Testing Port {port}...")
    try:
        # We use follow_redirects=False to see where uvicorn is being pushed
        with httpx.Client(proxy=proxy_url, timeout=10.0, follow_redirects=False) as client:
            resp = client.get("https://httpbin.org/ip")
            print(f"  Status: {resp.status_code}")
            if resp.status_code in [301, 302]:
                print(f"  Redirect Location: {resp.headers.get('Location')}")
            elif resp.status_code == 200:
                print(f"  Success! IP: {resp.json().get('origin')}")
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    for port in [80, 3128, 443, 8000]:
        test_proxy(port)
