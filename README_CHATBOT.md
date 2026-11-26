# Rifa Tasfia Portfolio: AI Client Assistant (setup and guide)

Only the chatbot brain was upgraded. `static/index.html` (the whole portfolio and the chat window) is byte-for-byte unchanged.

## Files
| File | Status | What it does |
|---|---|---|
| `main.py` | CHANGED | Server and chatbot logic: prompt, memory, validation, errors, notifications |
| `knowledge.json` | NEW | Your facts: services, projects, contact, hours, prices. **Edit this to teach the bot** |
| `requirements.txt` | FIXED | Was listing `anthropic`; now correct (`flask`, `google-genai`, `python-dotenv`, `gunicorn`) |
| `.env.example` | NEW | Template for secrets. Copy to `.env` |
| `.gitignore` | UPDATED | Also ignores `chat_log.jsonl` |
| `tests/test_chatbot_offline.py` | NEW | 21 offline tests (no API, no email) |
| `scenario_test.py` | NEW | 14 live client conversations to read yourself |
| `static/index.html`, `list_models.py`, `test_gemini.py` | UNCHANGED | |

## Run it
```
pip install -r requirements.txt
copy .env.example .env        (Mac/Linux: cp .env.example .env)
```
Open `.env`, paste your keys, then:
```
python main.py
```
Open http://127.0.0.1:5000 (not Live Server, the chatbot needs this server).

## API key
1. `GEMINI_API_KEY`: create at aistudio.google.com, paste after the `=` with no quotes or spaces.
2. `MODEL`: run `python list_models.py` and use a name from the list (for example `gemini-3.6-flash`). If the name is wrong or busy, the server automatically tries `FALLBACK_MODELS` and remembers the one that works.
3. `RESEND_API_KEY` + `NOTIFY_TO`: for inquiry emails (resend.com, sign up with the same email as `NOTIFY_TO`). Without them the chatbot still chats, but it will tell the visitor to contact you directly instead of claiming an email was sent.
4. After editing `.env`, stop the server (Ctrl+C) and start it again. On Render, set the same names in Environment Variables.

## Update your information
Edit `knowledge.json` (keep the quotes and commas). The bot reloads it automatically.
- New work: add an item in `portfolio_projects`.
- New service: copy one block in `services`.
- Prices: write `"starting_price": "From 1,500 BDT"` inside a service. Until you do, the bot will never quote any price.
- Typical delivery time: fill `typical_timeline`. Until then it will never promise one.
- Hours: change `open_hour` / `close_hour` (24-hour clock, Bangladesh time) and `hours_text`.
- Revisions, payment terms, refunds, working days: add them as facts in `background` or `faq` only when you decide them.

## Test
```
python -m unittest discover -s tests -v     (offline, instant)
python scenario_test.py                      (live, uses your key, prints every reply, sends no email)
```

## Deploy (always-on)
Same as before: push to GitHub, Render Web Service, build `pip install -r requirements.txt`, start `gunicorn main:app`, add the environment variables. Free Render sleeps after inactivity; the paid always-on plan avoids that. `chat_log.jsonl` is lost on Render restarts, so the email is your record.

## Limits (honest)
- Conversation memory lasts for the open chat (last 24 messages). It is lost when the visitor refreshes the page.
- Replies depend on Gemini and can occasionally be imperfect: read `scenario_test.py` output after any prompt change.
- Free Gemini and Resend tiers have limits; Gemini free-tier data may be used by Google, so consider a paid tier before collecting client details.
