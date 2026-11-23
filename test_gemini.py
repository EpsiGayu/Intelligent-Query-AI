
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

try:
    print("Testing Gemini API...")

    response = client.models.generate_content(
        model=model,
        contents="Hello. Reply with only: API WORKING"
    )

    print("SUCCESS:", response.text)

except Exception as error:
    print("FAILED:", error)
