
import base64
import re

def google_news_url_decoder(url: str):
    """
    Decodes Google News CBM tokens using Protobuf-style binary parsing.
    This bypasses Google's redirection jail entirely.
    """
    try:
        if "/articles/" not in url: return url
        
        token = url.split("/articles/")[1].split("?")[0]
        
        # 1. Base64 decode the token
        # Adding padding just in case
        padded = token + "=" * (-len(token) % 4)
        decoded_bytes = base64.urlsafe_b64decode(padded)
        
        # 2. Advanced Binary Extraction
        # In modern CBM tokens, the URL is nested. 
        # We look for the common 'http' pattern in the binary blob.
        # This is more robust than strict protobuf parsing which breaks often.
        
        # We look for a string that starts with http and ends before binary non-printables
        matches = re.findall(rb"https?://[^\x00-\x1F\x7F]+", decoded_bytes)
        
        if matches:
            # Sort by length and return the longest (usually the correct destination)
            matches.sort(key=len, reverse=True)
            return matches[0].decode('utf-8', errors='ignore')
            
        # Recursive check: Sometimes the URL is base64 encoded AGAIN inside the binary
        # We look for base64-like strings (min 30 chars) and try to decode them
        inner_matches = re.findall(rb"[A-Za-z0-9_-]{30,}", decoded_bytes)
        for inner in inner_matches:
            try:
                inner_padded = inner.decode() + "=="
                inner_decoded = base64.urlsafe_b64decode(inner_padded)
                final_matches = re.findall(rb"https?://[^\x00-\x1F\x7F]+", inner_decoded)
                if final_matches:
                    final_matches.sort(key=len, reverse=True)
                    return final_matches[0].decode('utf-8', errors='ignore')
            except: continue
            
    except Exception as e:
        print(f"Decoder Error: {e}")
    return url

url = "https://news.google.com/rss/articles/CBMiakFVX3lxTE96SXR2cGI3dHBReDYxbUw5bWZlUnhSTHY0Z3ZoYXlYVGRId0ZUQ0hrdWtNQlRtQTVEVlNmME1CZ0QtNWN5SHNqb0F4eHUtbVExcG5wNWZoaUdDcUNsRlFWeklSREFDNEl0UGc?oc=5"
print(f"Testing Deep Binary Decoder:")
print(google_news_url_decoder(url))
