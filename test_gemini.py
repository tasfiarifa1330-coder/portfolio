import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
key = os.getenv("GEMINI_API_KEY")
model = os.getenv("MODEL") or os.getenv("Model") or "gemini-3.6-flash"
print("Key found:", bool(key), "| Model:", model)
c = genai.Client(api_key=key)
try:
    r = c.models.generate_content(model=model, contents="Say hello in one short sentence.")
    print("OK ->", r.text)
except Exception as e:
    print("ERROR ->", e)
    print("\nModels available for your key (use one of these as MODEL):")
    try:
        for m in c.models.list():
            if "gemini" in m.name and "flash" in m.name:
                print("  ", m.name.replace("models/", ""))
    except Exception as e2:
        print("Could not list models:", e2)