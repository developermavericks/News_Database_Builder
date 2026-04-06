import requests, time

def test_sync():
    print("Sync GET to Bing (Requests)...")
    start = time.time()
    try:
        resp = requests.get("https://www.bing.com/news/search?q=AI&format=rss", timeout=10)
        print(f"BING SYNC RESP: {resp.status_code} in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"BING SYNC ERROR: {e}")

if __name__ == "__main__":
    test_sync()
