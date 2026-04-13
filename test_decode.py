
import base64
import re

def test_decode(url):
    try:
        encoded = url.split("/articles/")[1].split("?")[0]
        # Base64 with potential missing padding
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        print(f"Decoded Binary: {decoded}")
        
        match = re.search(rb"https?://[^\x00-\x1F\x7F]+", decoded)
        if match:
             print(f"Match: {match.group(0).decode('utf-8', errors='ignore')}")
        else:
             print("No HTTP match found in decoded binary.")
    except Exception as e:
        print(f"Error: {e}")

url = "https://news.google.com/rss/articles/CBMiakFVX3lxTE96SXR2cGI3dHBReDYxbUw5bWZlUnhSTHY0Z3ZoYXlYVGRId0ZUQ0hrdWtNQlRtQTVEVlNmME1CZ0QtNWN5SHNqb0F4eHUtbVExcG5wNWZoaUdDcUNsRlFWeklSREFDNEl0UGc?oc=5"
test_decode(url)
