
import base64
import re

def test_recursive_decode(token):
    try:
        # Some tokens have a prefix like \x08\x13"j
        # We look for the start of what might be a base64 string
        # Typically Google News URLs use urlsafe base64 (A-Za-z0-9-_)
        match = re.search(r"[A-Za-z0-9_-]{30,}", token)
        if match:
             inner = match.group(0)
             print(f"Inner Token: {inner}")
             padded = inner + "=" * (-len(inner) % 4)
             decoded = base64.urlsafe_b64decode(padded)
             print(f"Inner Decoded: {decoded}")
             
             # Search for URL again
             url_match = re.search(rb"https?://[^\x00-\x1F\x7F]+", decoded)
             if url_match:
                  print(f"SUCCESS: {url_match.group(0).decode('utf-8')}")
             else:
                  # Try to see if it's ANOTHER layer or just binary with the host
                  print("No final URL found in inner layer.")
    except Exception as e:
        print(f"Error: {e}")

# The user's token from the URL
token = "CBMiakFVX3lxTE96SXR2cGI3dHBReDYxbUw5bWZlUnhSTHY0Z3ZoYXlYVGRId0ZUQ0hrdWtNQlRtQTVEVlNmME1CZ0QtNWN5SHNqb0F4eHUtbVExcG5wNWZoaUdDcUNsRlFWeklSREFDNEl0UGc"
test_recursive_decode(token)
