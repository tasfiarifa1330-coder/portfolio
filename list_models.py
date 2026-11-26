import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
key = os.getenv("GEMINI_API_KEY")

print("Key found:", bool(key))
if key:
    print("Key starts with:", key[:8] + "...")
    print("Key length:", len(key))

if not key:
    print("\n❌ GEMINI_API_KEY is missing in .env")
    print("Add this line to .env: GEMINI_API_KEY=AIzaSy...your-key")
    exit(1)

client = genai.Client(api_key=key)

print("\n=== Models available for your key ===\n")
try:
    for m in client.models.list():
        if "generateContent" in (m.supported_actions or []):
            name = m.name.replace("models/", "")
            print("  ", name)
except Exception as e:
    print("ERROR listing models:", e)