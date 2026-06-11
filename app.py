import os
import re
import logging
from io import BytesIO
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, request, jsonify
from supabase import create_client, Client
from pypdf import PdfReader
from docx import Document

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("justice_ai_pro")

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY")
if not SUPABASE_URL:
    raise RuntimeError("Missing SUPABASE_URL")
if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

USER_STATES = {}

PLANS = {
    "1m": {"months": 1, "stars": 100, "lt": "1 mėnuo", "en": "1 month", "no": "1 måned"},
    "3m": {"months": 3, "stars": 250, "lt": "3 mėnesiai", "en": "3 months", "no": "3 måneder"},
    "6m": {"months": 6, "stars": 450, "lt": "6 mėnesiai", "en": "6 months", "no": "6 måneder"},
    "12m": {"months": 12, "stars": 800, "lt": "12 mėnesių", "en": "12 months", "no": "12 måneder"},
}

COUNTRIES = {
    "lt": {"lt": "Lietuva", "en": "Lithuania", "no": "Litauen"},
    "no": {"lt": "Norvegija", "en": "Norway", "no": "Norge"},
    "uk": {"lt": "Jungtinė Karalystė", "en": "United Kingdom", "no": "Storbritannia"},
    "other": {"lt": "Kita šalis", "en": "Other country", "no": "Annet land"},
}

SYSTEM_PROMPT = """
You are Justice AI, a consumer rights and legal claims assistant.

Language:
- Reply only in the selected user language.
- Never mix languages.

Jurisdiction:
- Apply the selected country/jurisdiction.
- If selected jurisdiction is Norway, do not answer as if only Lithuanian law is available. Use Norwegian consumer law and relevant Norwegian criminal law sections where applicable.
- If selected jurisdiction is United Kingdom, use UK law. If selected jurisdiction is Lithuania, use Lithuanian law.
- Always include relevant legal acts with article/section numbers when possible.
- Never state that a crime definitely occurred.
- Use cautious wording such as: possibly applicable, may be relevant, may be assessed under.

Style:
- Use grammatically correct, natural and professional language.
- Avoid repetition.
- Use clear complete sentences.
- Do not use greetings such as Dear Sir/Madam, Gerbiamasis, Gerbiamoji, Gerb. pone, Gerb. ponia.
- Do not use the phrase [Paraiška pridedama].
- In Lithuanian legal text, avoid the word "normaliai". Use "įprastai", "pagal paskirtį" or "įprastomis naudojimo sąlygomis".

Rules:
- Do not claim to be a lawyer.
- Do not suggest consulting lawyers or external specialists.
- Provide practical next steps yourself.
- Mention institutions only as concrete action targets.
- Separate facts from assumptions.
"""

WELCOME_TEXT_LT = """⚖️ JUSTICE AI

Vartotojų teisių apsaugos ir teisinių pretenzijų sistema

Padedu spręsti:

📦 Internetinių pirkimų problemas
💳 Pinigų grąžinimo klausimus
📄 Pretenzijų ir skundų rengimą
⚖️ Sutarčių ir paslaugų ginčus
🚨 Galimus sukčiavimo atvejus"""

WELCOME_TEXT_EN = """⚖️ JUSTICE AI

Consumer Rights & Legal Claims Assistant

I can help with:

📦 Online purchase problems
💳 Refunds and payment disputes
📄 Complaints and claims
⚖️ Contract and service disputes
🚨 Possible fraud cases"""

WELCOME_TEXT_NO = """⚖️ JUSTICE AI

Assistent for forbrukerrettigheter og juridiske krav

Jeg kan hjelpe med:

📦 Problemer med nettkjøp
💳 Refusjon og betalingskonflikter
📄 Klager og krav
⚖️ Kontrakt- og tjenestetvister
🚨 Mulige svindelsaker"""

SAFETY_TEXT = {
    "lt": """🔐 Saugumas ir konfidencialumas

Naudojamos platformos:

🤖 OpenAI
☁️ Supabase
🌐 Render
✈️ Telegram

• Jūsų pateikta informacija ir duomenys bus saugomi iki prenumeratos pabaigos.
• Pasibaigus prenumeratai, duomenys ištrinami.

✅ Suprantu ir sutinku""",
    "en": """🔐 Security and confidentiality

Platforms used:

🤖 OpenAI
☁️ Supabase
🌐 Render
✈️ Telegram

• Your submitted information and data will be stored until the subscription ends.
• After the subscription ends, the data is deleted.

✅ I understand and agree""",
    "no": """🔐 Sikkerhet og konfidensialitet

Plattformer som brukes:

🤖 OpenAI
☁️ Supabase
🌐 Render
✈️ Telegram

• Informasjonen og dataene du sender inn lagres til abonnementet avsluttes.
• Etter at abonnementet avsluttes, slettes dataene.

✅ Jeg forstår og godtar""",
}

TEXT = {
    "lt": {
        "accept": "✅ Suprantu ir sutinku",
        "choose_country": "🌍 Pasirinkite šalį:",
        "collect_case": "📋 Surinkime duomenis bylai\n\nTrumpai aprašykite situaciją arba įkelkite dokumentą.\n\nKuo daugiau informacijos pateiksite, tuo tikslesnė bus analizė.",
        "thinking": "⚖️ Analizuoju pateiktą informaciją...",
        "case_created": "📁 Byla sukurta\n\n🆔 Bylos Nr.: {case_number}",
        "initial": "🔎 Tai pirminis bylos vertinimas.\n\nNorint gauti išsamų vertinimą, veiksmų planą ir dokumentų projektus, pasirinkite prieigos laikotarpį.",
        "choose_plan": "🔓 Pasirinkite prieigos laikotarpį:",
        "active_until": "✅ Prieiga aktyvi iki:",
        "access_active": "✅ Prieiga aktyvuota.",
        "test_active": "🧪 TEST REŽIMAS: prieiga aktyvuota.",
        "after_full": "❓ Norite patikslinti bylą?\n\nGalite užduoti papildomą klausimą arba sugeneruoti dokumentą pagal šią bylą.",
        "ask_question": "❓ Užduoti klausimą",
        "generate_doc": "📄 Generuoti dokumentą",
        "ask_prompt": "❓ Parašykite papildomą klausimą dėl šios bylos.",
        "choose_doc": "📄 Pasirinkite, kokį dokumentą norite sugeneruoti:",
        "no_case": "Pirmiausia aprašykite situaciją arba įkelkite dokumentą.",
        "doc_thinking": "⏳ Ruošiu dokumentą...",
        "file_received": "📎 Failas gautas. Apdoroju dokumentą...",
        "file_saved": "📎 Dokumentas pridėtas prie bylos.",
        "file_no_text": "⚠️ Failą išsaugojau, bet teksto nuskaityti nepavyko. Trumpai aprašykite, kas jame svarbu.",
    },
    "en": {
        "accept": "✅ I understand and agree",
        "choose_country": "🌍 Select country:",
        "collect_case": "📋 Let us collect case details\n\nBriefly describe the situation or upload a document.\n\nThe more information you provide, the more accurate the assessment will be.",
        "thinking": "⚖️ Analyzing the submitted information...",
        "case_created": "📁 Case created\n\n🆔 Case No.: {case_number}",
        "initial": "🔎 This is an initial case assessment.\n\nTo receive a full review, action plan and document drafts, choose an access period.",
        "choose_plan": "🔓 Choose access period:",
        "active_until": "✅ Access active until:",
        "access_active": "✅ Access activated.",
        "test_active": "🧪 TEST MODE: access activated.",
        "after_full": "❓ Would you like to clarify the case?\n\nYou can ask a follow-up question or generate a document based on this case.",
        "ask_question": "❓ Ask a question",
        "generate_doc": "📄 Generate document",
        "ask_prompt": "❓ Write your follow-up question about this case.",
        "choose_doc": "📄 Choose which document you want to generate:",
        "no_case": "Please describe the situation or upload a document first.",
        "doc_thinking": "⏳ Preparing document...",
        "file_received": "📎 File received. Processing the document...",
        "file_saved": "📎 Document added to the case.",
        "file_no_text": "⚠️ I saved the file, but could not extract text. Briefly describe what is important in it.",
    },
    "no": {
        "accept": "✅ Jeg forstår og godtar",
        "choose_country": "🌍 Velg land:",
        "collect_case": "📋 La oss samle saksinformasjon\n\nBeskriv situasjonen kort eller last opp et dokument.\n\nJo mer informasjon du gir, desto mer presis blir vurderingen.",
        "thinking": "⚖️ Analyserer innsendt informasjon...",
        "case_created": "📁 Sak opprettet\n\n🆔 Saksnr.: {case_number}",
        "initial": "🔎 Dette er en første vurdering av saken.\n\nFor full vurdering, handlingsplan og dokumentutkast, velg tilgangsperiode.",
        "choose_plan": "🔓 Velg tilgangsperiode:",
        "active_until": "✅ Tilgang aktiv til:",
        "access_active": "✅ Tilgang aktivert.",
        "test_active": "🧪 TESTMODUS: tilgang aktivert.",
        "after_full": "❓ Vil du avklare saken videre?\n\nDu kan stille et oppfølgingsspørsmål eller generere et dokument basert på saken.",
        "ask_question": "❓ Still spørsmål",
        "generate_doc": "📄 Generer dokument",
        "ask_prompt": "❓ Skriv oppfølgingsspørsmålet ditt om denne saken.",
        "choose_doc": "📄 Velg hvilket dokument du vil generere:",
        "no_case": "Beskriv situasjonen eller last opp et dokument først.",
        "doc_thinking": "⏳ Forbereder dokument...",
        "file_received": "📎 Fil mottatt. Behandler dokumentet...",
        "file_saved": "📎 Dokumentet er lagt til saken.",
        "file_no_text": "⚠️ Filen er lagret, men tekst kunne ikke leses. Beskriv kort hva som er viktig i den.",
    },
}


def detect_lang(user: dict) -> str:
    code = (user.get("language_code") or "").lower()
    if code.startswith("lt"):
        return "lt"
    if code.startswith("no") or code.startswith("nb") or code.startswith("nn"):
        return "no"
    return "en"


def welcome_text(lang: str) -> str:
    if lang == "lt":
        return WELCOME_TEXT_LT
    if lang == "no":
        return WELCOME_TEXT_NO
    return WELCOME_TEXT_EN


def lang_name(lang: str) -> str:
    return {"lt": "Lithuanian", "no": "Norwegian", "en": "English"}.get(lang, "English")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def add_months(months: int) -> datetime:
    return now_utc() + timedelta(days=30 * months)


def get_state(chat_id: int) -> dict:
    if chat_id not in USER_STATES:
        USER_STATES[chat_id] = {
            "lang": "en",
            "accepted": False,
            "country": None,
            "case_text": None,
            "case_id": None,
            "case_number": None,
            "awaiting_followup": False,
        }
    return USER_STATES[chat_id]


def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=30)
    if not r.ok:
        logger.error("Telegram send error: %s %s", r.status_code, r.text)
    return r.json() if r.ok else None


def send_chunks(chat_id: int, text: str, reply_markup: dict | None = None):
    if not text:
        return
    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
    for idx, chunk in enumerate(chunks):
        send_message(chat_id, chunk, reply_markup if idx == len(chunks) - 1 else None)


def answer_callback(callback_query_id: str, text: str = ""):
    requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": callback_query_id, "text": text}, timeout=10)


def safety_menu(lang: str):
    return {"inline_keyboard": [[{"text": TEXT[lang]["accept"], "callback_data": "accept_safety"}]]}


def country_menu():
    return {"inline_keyboard": [
        [{"text": "🇱🇹 Lietuva", "callback_data": "country_lt"}],
        [{"text": "🇳🇴 Norvegija", "callback_data": "country_no"}],
        [{"text": "🇬🇧 Jungtinė Karalystė", "callback_data": "country_uk"}],
        [{"text": "🌐 Kita šalis", "callback_data": "country_other"}],
    ]}


def plan_menu(lang: str):
    labels = []
    for key, plan in PLANS.items():
        labels.append([{"text": f"⭐ {plan['stars']} - {plan[lang]}", "callback_data": f"plan_{key}"}])
    return {"inline_keyboard": labels}


def after_full_menu(lang: str):
    return {"inline_keyboard": [
        [{"text": TEXT[lang]["ask_question"], "callback_data": "ask_question"}],
        [{"text": TEXT[lang]["generate_doc"], "callback_data": "show_docs"}],
    ]}


def detect_case_type(case_text: str) -> str:
    text = (case_text or "").lower()
    fraud_keywords = [
        "sukči", "apgav", "scam", "fraud", "deep", "coin", "crypto", "kript", "binance",
        "invest", "whatsapp", "telegram", "anydesk", "platform", "netikra", "praradau pinigus"
    ]
    bank_keywords = ["bank", "kortel", "visa", "mastercard", "mokėj", "paved", "chargeback", "lėšų grąž"]
    seller_keywords = ["prek", "pardav", "užsak", "siunt", "nepristat", "negav", "brok", "garant", "defekt", "grąžinti"]
    service_keywords = ["sutart", "paslaug", "prenumer", "operator", "internetas", "ryšys"]

    if any(k in text for k in fraud_keywords):
        return "fraud"
    if any(k in text for k in bank_keywords):
        return "bank"
    if any(k in text for k in seller_keywords):
        return "seller"
    if any(k in text for k in service_keywords):
        return "service"
    return "general"


def doc_label(doc_type: str, lang: str, country: str | None = None) -> str:
    country = country or "lt"
    labels = {
        "lt": {
            "seller_claim": "📄 Pretenzija pardavėjui",
            "bank_refund": "💳 Prašymas bankui dėl pinigų grąžinimo",
            "authority_complaint": "⚖️ Skundas institucijai",
            "fraud_report": "🚨 Pareiškimas dėl galimo sukčiavimo",
        },
        "no": {
            "seller_claim": "📄 Klagebrev til selger",
            "bank_refund": "💳 Forespørsel til bank",
            "authority_complaint": "⚖️ Klage til myndighet",
            "fraud_report": "🚨 Svindelrapport",
        },
        "en": {
            "seller_claim": "📄 Seller complaint letter",
            "bank_refund": "💳 Bank refund request",
            "authority_complaint": "⚖️ Authority complaint",
            "fraud_report": "🚨 Fraud report",
        },
    }
    return labels.get(lang, labels["en"]).get(doc_type, doc_type)


def relevant_doc_types(case_text: str, country: str | None = None) -> list[str]:
    case_type = detect_case_type(case_text)
    if case_type == "fraud":
        return ["fraud_report", "bank_refund"]
    if case_type == "bank":
        return ["bank_refund", "authority_complaint"]
    if case_type == "seller":
        return ["seller_claim", "bank_refund", "authority_complaint"]
    if case_type == "service":
        return ["seller_claim", "authority_complaint"]
    return ["authority_complaint", "seller_claim"]


def doc_menu(lang: str, case_text: str = "", country: str | None = None):
    rows = []
    for doc_type in relevant_doc_types(case_text, country):
        rows.append([{"text": doc_label(doc_type, lang, country), "callback_data": f"doc_{doc_type}"}])
    return {"inline_keyboard": rows}


def db_user_get(telegram_id: int):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    return res.data[0] if res.data else None


def db_user_upsert(tg_user: dict, lang: str | None = None):
    telegram_id = tg_user.get("id")
    username = tg_user.get("username")
    data = {"telegram_id": telegram_id, "username": username}
    if lang:
        data["language"] = lang
    existing = db_user_get(telegram_id)
    if existing:
        supabase.table("users").update(data).eq("telegram_id", telegram_id).execute()
    else:
        data["language"] = lang or "lt"
        supabase.table("users").insert(data).execute()


def db_set_subscription(telegram_id: int, plan_name: str, months: int, stars: int):
    until = add_months(months)
    supabase.table("users").update({"subscription_until": iso(until), "plan_name": plan_name}).eq("telegram_id", telegram_id).execute()
    supabase.table("payments").insert({"telegram_id": telegram_id, "plan_name": plan_name, "stars_paid": stars, "valid_until": iso(until)}).execute()
    return until


def db_subscription_active(telegram_id: int):
    user = db_user_get(telegram_id)
    if not user:
        return False, None
    until = parse_dt(user.get("subscription_until"))
    if not until:
        return False, None
    return until > now_utc(), until


def db_create_case(telegram_id: int, case_text: str):
    title = (case_text or "New case")[:80]
    res = supabase.table("cases").insert({"telegram_id": telegram_id, "title": title, "case_text": case_text, "status": "open"}).execute()
    case = res.data[0]
    case_id = case["id"]
    case_number = f"CASE-{now_utc().year}-{case_id:06d}"
    supabase.table("cases").update({"case_number": case_number}).eq("id", case_id).execute()
    return case_id, case_number


def db_update_case(case_id: int, case_text: str):
    if case_id:
        supabase.table("cases").update({"case_text": case_text}).eq("id", case_id).execute()


def db_add_case_file(case_id: int, file_name: str, file_type: str, telegram_file_id: str):
    if case_id:
        supabase.table("case_files").insert({"case_id": case_id, "file_name": file_name, "file_type": file_type, "telegram_file_id": telegram_file_id}).execute()


def extract_text_from_file(file_name: str, file_bytes: bytes) -> str:
    lower = (file_name or "").lower()
    try:
        if lower.endswith(".txt"):
            return file_bytes.decode("utf-8", errors="ignore")
        if lower.endswith(".pdf"):
            reader = PdfReader(BytesIO(file_bytes))
            return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        if lower.endswith(".docx"):
            doc = Document(BytesIO(file_bytes))
            return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception as e:
        logger.warning("File extraction failed: %s", e)
    return ""


def openai_request(prompt: str, lang: str) -> str:
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + f"\nReply only in {lang_name(lang)}."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    if not r.ok:
        logger.error("OpenAI error: %s %s", r.status_code, r.text)
        return ""
    return r.json()["choices"][0]["message"]["content"].strip()


def build_case_prompt(case_text: str, lang: str, country: str, full: bool):
    country_name = COUNTRIES.get(country, COUNTRIES["lt"])[lang]
    if lang == "lt":
        if full:
            return f"""
Atsakyk tik lietuviškai. Taikoma šalis / jurisdikcija: {country_name}.

Paruošk išsamų bylos vertinimą:

📁 Bylos numeris
📋 Situacijos santrauka
📌 Pagrindiniai faktai
⚖️ Galimai taikytini teisės aktai
Nurodyk įstatymų, direktyvų arba kodeksų pavadinimus ir straipsnių / paragrafų numerius, jei jie gali būti aktualūs.
⚠️ Galimi pažeidimai
📂 Reikalingi įrodymai
📊 Bylos stiprumas 0-100
🎯 Veiksmų planas
📄 Galimi dokumentai
💳 Galimybė susigrąžinti pinigus per banką, jei aktualu
✅ Išvada

🧭 Klausimai bylai patikslinti
Pateik ne daugiau kaip 2 svarbiausius klausimus. Jei klausimai nebūtini, šio skyriaus nerodyk.

💡 Praktiniai pasiūlymai
Pateik ne daugiau kaip 2 konkrečius pasiūlymus.

Neteik kategoriško teiginio, kad nusikaltimas įvykdytas. Naudok: „galimai taikytina“, „gali būti aktualu“, „gali būti vertinama pagal“.

Bylos informacija:
{case_text}
"""
        return f"""
Atsakyk tik lietuviškai. Taikoma šalis / jurisdikcija: {country_name}.

Paruošk pirminį bylos vertinimą:

📋 Kategorija
⚠️ Pagrindinė problema
⚖️ Galimai taikytini teisės aktai
📂 Trūkstami įrodymai
🎯 Pirmas rekomenduojamas veiksmas
🔓 Išplėstinio atsakymo galimybė

Pateik ne daugiau kaip 2 klausimus ir ne daugiau kaip 2 pasiūlymus.

Situacija:
{case_text}
"""

    if lang == "no":
        return f"""
Svar kun på norsk. Jurisdiksjon: {country_name}.

Lag {'en full vurdering' if full else 'en kort førstevurdering'} med relevante lover og paragrafnumre når mulig. Ikke påstå at en straffbar handling definitivt har skjedd. Maks 2 spørsmål og 2 forslag.

Saksinformasjon:
{case_text}
"""

    return f"""
Answer only in English. Jurisdiction: {country_name}.

Prepare {'a full case review' if full else 'a short initial assessment'} with relevant law names and article/section numbers where possible. Do not state that a crime definitely occurred. Max 2 questions and 2 suggestions.

Case information:
{case_text}
"""


def document_name(doc_type: str, lang: str) -> str:
    names = {
        "lt": {
            "seller_claim": "pretenziją pardavėjui",
            "bank_refund": "prašymą bankui dėl pinigų grąžinimo",
            "authority_complaint": "skundą institucijai",
            "fraud_report": "pareiškimą dėl galimo sukčiavimo",
        },
        "en": {
            "seller_claim": "seller complaint letter",
            "bank_refund": "bank refund request",
            "authority_complaint": "consumer authority complaint",
            "fraud_report": "fraud report",
        },
        "no": {
            "seller_claim": "klagebrev til selger",
            "bank_refund": "forespørsel til bank om tilbakebetaling",
            "authority_complaint": "klage til myndighet",
            "fraud_report": "svindelrapport",
        },
    }
    return names.get(lang, names["en"]).get(doc_type, "document")


def jurisdiction_recipient_hint(country: str, doc_type: str, lang: str) -> str:
    if country == "no":
        if doc_type == "fraud_report":
            return "Norvegijos policija / Politiet. Jei dokumentas skirtas vartotojų ginčui, naudok Forbrukertilsynet."
        if doc_type == "authority_complaint":
            return "Forbrukertilsynet (Norwegian Consumer Authority) arba Forbrukerrådet, pagal situaciją."
        if doc_type == "bank_refund":
            return "Kliento bankas Norvegijoje arba mokėjimo kortelės išdavėjas."
        return "Pardavėjas arba paslaugos teikėjas Norvegijoje."
    if country == "uk":
        if doc_type == "fraud_report":
            return "Action Fraud / UK Police, jei yra sukčiavimo požymių."
        if doc_type == "authority_complaint":
            return "Trading Standards arba Citizens Advice Consumer Service, pagal situaciją."
        if doc_type == "bank_refund":
            return "Kliento bankas / kortelės išdavėjas Jungtinėje Karalystėje."
        return "Pardavėjas arba paslaugos teikėjas Jungtinėje Karalystėje."
    if country == "other":
        return "Parink instituciją pagal byloje nurodytą šalį. Jei šalies nėra, dokumente nenaudok konkrečios institucijos pavadinimo."
    # Lithuania default
    if doc_type == "fraud_report":
        return "Policija / ePolicija, jei yra galimo sukčiavimo požymių."
    if doc_type == "authority_complaint":
        return "Valstybinė vartotojų teisių apsaugos tarnyba, jei tai vartotojų ginčas; Lietuvos bankas, jei tai finansinių paslaugų ginčas."
    if doc_type == "bank_refund":
        return "Kliento bankas arba mokėjimo kortelės išdavėjas."
    return "Pardavėjas arba paslaugos teikėjas."


def build_doc_prompt(case_text: str, lang: str, country: str, doc_type: str):
    country_name = COUNTRIES.get(country, COUNTRIES["lt"])[lang]
    doc = document_name(doc_type, lang)
    recipient_hint = jurisdiction_recipient_hint(country, doc_type, lang)
    case_type = detect_case_type(case_text)

    if lang == "lt":
        return f"""
Atsakyk tik lietuviškai. Taikoma šalis / jurisdikcija: {country_name}.

Paruošk dokumentą: {doc}.
Bylos tipas pagal turinį: {case_type}.
Adresato parinkimo gairė: {recipient_hint}

Svarbiausios taisyklės:
- Dokumentą generuok iš realios bylos informacijos ir iš įkeltų failų nuskaityto teksto.
- Jei PDF / bylos tekste yra vardas, pavardė, adresas, el. paštas, telefonas, data, suma, platforma, įmonė, bankas, pavedimų duomenys ar kiti faktai, įrašyk juos į dokumentą automatiškai.
- Nenaudok tuščių laukų [Vardas Pavardė], [Adresas], [El. paštas], [Telefonas], [Data], jei duomenys jau randami byloje.
- Jei konkretaus duomens nėra, jo eilutę praleisk. Neįterpk instrukcinių tekstų laužtiniuose skliaustuose.
- Nenaudok frazių: [Čia pateikite...], [Jei yra priedų...], [Paraiška pridedama].
- Dokumentas turi būti pilnai užpildytas ir paruoštas siuntimui.
- Dokumentą pradėk nuo tinkamo gavėjo pagal pasirinktą šalį ir dokumento antraštės.
- Jei pasirinkta Norvegija, nenaudok Lietuvos institucijų, nebent byloje aiškiai nurodyta, kad ginčas nagrinėjamas Lietuvoje.
- Jei pasirinkta Jungtinė Karalystė, nenaudok Lietuvos institucijų.
- Nenaudok kreipinių: Gerbiamasis, Gerbiamoji, Gerb. pone, Gerb. ponia.
- Dokumento viršuje gali įrašyti: „Sukūrė Justice AI“. Bylos numerio dokumente nerašyk, nebent jis būtinas vidinei nuorodai.
- Įtrauk galimai taikytinus teisės aktus su straipsnių / paragrafų numeriais, bet neteik kategoriško teiginio, kad nusikaltimas įvykdytas.
- Jei yra aiškūs priedai pagal bylą, naudok skyrių „Priedai:“ ir išvardink tik realiai byloje minimus priedus. Jei priedų neįmanoma nustatyti, šio skyriaus nerodyk.
- Nesiūlyk kreiptis į konsultantus ar teisininkus.

Bylos informacija ir nuskaitytas dokumentų tekstas:
{case_text}
"""

    return f"""
Reply only in {lang_name(lang)}. Jurisdiction: {country_name}.

Prepare this document: {doc}.
Case type from content: {case_type}.
Recipient guidance: {recipient_hint}

Rules:
- Generate the document from the real case information and extracted file text.
- If the case/PDF contains name, address, email, phone, dates, amounts, platform names, company names, bank/payment data or other facts, insert them directly.
- Do not use empty placeholders if data exists.
- If a data point is missing, omit that line instead of adding instructional bracket text.
- Do not use instructional phrases like [insert details here].
- The document must be complete and ready to send.
- Start with the correct recipient for the selected jurisdiction and the document title.
- If Norway is selected, do not use Lithuanian institutions unless the case explicitly concerns Lithuania.
- If the UK is selected, do not use Lithuanian institutions.
- Do not use Dear Sir/Madam or gendered salutations.
- You may write "Prepared via Justice AI" at the top. Do not include the case number unless it is necessary as an internal reference.
- Include relevant laws and section/article numbers, but do not state that a crime definitely occurred.
- If real attachments are identifiable from the case, include an Attachments section. Otherwise omit it.
- Do not suggest external lawyers or advisors.

Case information and extracted document text:
{case_text}
"""


def build_followup_prompt(case_text: str, question: str, lang: str, country: str):
    country_name = COUNTRIES.get(country, COUNTRIES["lt"])[lang]
    if lang == "lt":
        return f"""
Atsakyk tik lietuviškai. Taikoma šalis / jurisdikcija: {country_name}.
Atsakyk į papildomą klausimą pagal bylą. Būk konkretus, aiškus ir praktiškas. Jei aktualu, nurodyk teisės akto straipsnį.

Bylos informacija:
{case_text}

Klausimas:
{question}
"""
    return f"""
Reply only in {lang_name(lang)}. Jurisdiction: {country_name}.
Answer the follow-up question based on the case. Be clear and practical. Mention legal sections if relevant.

Case information:
{case_text}

Question:
{question}
"""


def show_start(chat_id: int, tg_user: dict):
    lang = detect_lang(tg_user)
    state = get_state(chat_id)
    state.update({"lang": lang, "accepted": False, "country": None, "case_text": None, "case_id": None, "case_number": None, "awaiting_followup": False})
    db_user_upsert(tg_user, lang)
    start_text = f"{welcome_text(lang)}\n\n{SAFETY_TEXT[lang]}"
    send_message(chat_id, start_text, reply_markup=safety_menu(lang))


def show_country(chat_id: int):
    lang = get_state(chat_id)["lang"]
    send_message(chat_id, TEXT[lang]["choose_country"], reply_markup=country_menu())


def show_collect_case(chat_id: int):
    state = get_state(chat_id)
    send_message(chat_id, TEXT[state["lang"]]["collect_case"])


def create_case_from_text(chat_id: int, user_text: str):
    state = get_state(chat_id)
    lang = state["lang"]
    country = state.get("country") or "lt"
    case_id, case_number = db_create_case(chat_id, user_text)
    state.update({"case_text": user_text, "case_id": case_id, "case_number": case_number, "awaiting_followup": False})
    send_message(chat_id, TEXT[lang]["case_created"].format(case_number=case_number))

    active, until = db_subscription_active(chat_id)
    if active:
        send_message(chat_id, f"{TEXT[lang]['active_until']} {until.date()}")
        send_message(chat_id, TEXT[lang]["thinking"])
        full_text = f"Bylos numeris: {case_number}\n\n{user_text}"
        answer = openai_request(build_case_prompt(full_text, lang, country, full=True), lang)
        if not answer:
            send_message(chat_id, "⚠️ Analizės šiuo metu nepavyko atlikti. Bandykite dar kartą po kelių minučių." if lang == "lt" else "⚠️ The analysis could not be completed right now. Please try again in a few minutes.")
            return
        send_chunks(chat_id, answer, reply_markup=after_full_menu(lang))
        return

    send_message(chat_id, TEXT[lang]["thinking"])
    answer = openai_request(build_case_prompt(user_text, lang, country, full=False), lang)
    if not answer:
        send_message(chat_id, "⚠️ Analizės šiuo metu nepavyko atlikti. Bandykite dar kartą po kelių minučių." if lang == "lt" else "⚠️ The analysis could not be completed right now. Please try again in a few minutes.")
        return
    send_chunks(chat_id, answer)
    send_message(chat_id, TEXT[lang]["initial"])
    send_message(chat_id, TEXT[lang]["choose_plan"], reply_markup=plan_menu(lang))


def handle_file(chat_id: int, msg: dict):
    state = get_state(chat_id)
    lang = state["lang"]
    country = state.get("country") or "lt"

    file_id = None
    file_name = "uploaded_file"
    file_type = "unknown"

    if msg.get("document"):
        doc = msg["document"]
        file_id = doc.get("file_id")
        file_name = doc.get("file_name") or "document"
        file_type = doc.get("mime_type") or "document"
    elif msg.get("photo"):
        photo = msg["photo"][-1]
        file_id = photo.get("file_id")
        file_name = "photo.jpg"
        file_type = "image/jpeg"

    if not file_id:
        send_message(chat_id, TEXT[lang]["file_no_text"])
        return

    f_res = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=20).json()
    file_path = f_res.get("result", {}).get("file_path")
    file_bytes = b""
    if file_path:
        file_bytes = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}", timeout=60).content

    extracted = extract_text_from_file(file_name, file_bytes)

    created_now = False
    if not state.get("case_id"):
        base = f"Įkelta byla iš failo: {file_name}" if lang == "lt" else f"Case created from file: {file_name}"
        case_id, case_number = db_create_case(chat_id, base)
        state.update({"case_id": case_id, "case_number": case_number, "case_text": base})
        created_now = True

    db_add_case_file(state["case_id"], file_name, file_type, file_id)

    if extracted:
        state["case_text"] = (state.get("case_text") or "") + f"\n\n--- FILE: {file_name} ---\n{extracted[:6000]}"
        db_update_case(state["case_id"], state["case_text"])
        if lang == "lt":
            status_text = (
                f"📁 Byla sukurta\n\n"
                f"🆔 Bylos Nr.: {state.get('case_number')}\n\n"
                f"📎 Dokumentas pridėtas prie bylos.\n\n"
                f"⚖️ Analizuoju pateiktą informaciją..."
            ) if created_now else (
                f"📎 Dokumentas pridėtas prie bylos.\n\n"
                f"⚖️ Analizuoju pateiktą informaciją..."
            )
        elif lang == "no":
            status_text = (
                f"📁 Sak opprettet\n\n"
                f"🆔 Saksnr.: {state.get('case_number')}\n\n"
                f"📎 Dokumentet er lagt til saken.\n\n"
                f"⚖️ Analyserer innsendt informasjon..."
            ) if created_now else (
                f"📎 Dokumentet er lagt til saken.\n\n"
                f"⚖️ Analyserer innsendt informasjon..."
            )
        else:
            status_text = (
                f"📁 Case created\n\n"
                f"🆔 Case No.: {state.get('case_number')}\n\n"
                f"📎 Document added to the case.\n\n"
                f"⚖️ Analyzing the submitted information..."
            ) if created_now else (
                f"📎 Document added to the case.\n\n"
                f"⚖️ Analyzing the submitted information..."
            )
        send_message(chat_id, status_text)
    else:
        send_message(chat_id, TEXT[lang]["file_no_text"])

    active, _ = db_subscription_active(chat_id)
    if active:
        full_text = f"Bylos numeris: {state.get('case_number')}\n\n{state.get('case_text')}"
        answer = openai_request(build_case_prompt(full_text, lang, country, full=True), lang)
        if not answer:
            err = "⚠️ Analizės šiuo metu nepavyko atlikti. Bandykite dar kartą po kelių minučių." if lang == "lt" else "⚠️ The analysis could not be completed right now. Please try again in a few minutes."
            send_message(chat_id, err)
            return
        send_chunks(chat_id, answer, reply_markup=after_full_menu(lang))
    else:
        send_message(chat_id, TEXT[lang]["initial"])
        send_message(chat_id, TEXT[lang]["choose_plan"], reply_markup=plan_menu(lang))


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Justice AI PRO", "telegram": bool(TELEGRAM_BOT_TOKEN), "supabase": bool(SUPABASE_URL)})


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(force=True, silent=True) or {}
    try:
        if "callback_query" in update:
            cq = update["callback_query"]
            callback_id = cq.get("id")
            data = cq.get("data", "")
            msg = cq.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            user = cq.get("from", {})
            answer_callback(callback_id)
            if not chat_id:
                return jsonify({"ok": True})

            state = get_state(chat_id)
            lang = state.get("lang") or detect_lang(user)

            if data == "accept_safety":
                state["accepted"] = True
                show_country(chat_id)

            elif data.startswith("country_"):
                country = data.replace("country_", "")
                state["country"] = country
                db_user_upsert(user, lang)
                show_collect_case(chat_id)

            elif data.startswith("plan_"):
                plan_key = data.replace("plan_", "")
                plan = PLANS.get(plan_key)
                if not plan:
                    return jsonify({"ok": True})
                if TEST_MODE:
                    until = db_set_subscription(chat_id, plan_key, plan["months"], plan["stars"])
                    send_message(chat_id, f"{TEXT[lang]['test_active']}\n{TEXT[lang]['active_until']} {until.date()}")
                    if state.get("case_text"):
                        send_message(chat_id, TEXT[lang]["thinking"])
                        full_text = f"Bylos numeris: {state.get('case_number')}\n\n{state.get('case_text')}"
                        answer = openai_request(build_case_prompt(full_text, lang, state.get("country") or "lt", full=True), lang)
                        send_chunks(chat_id, answer, reply_markup=after_full_menu(lang))
                    return jsonify({"ok": True})
                description = f"{plan[lang]} access"
                payload = f"sub|{plan_key}|{chat_id}"
                requests.post(f"{TELEGRAM_API}/sendInvoice", json={
                    "chat_id": chat_id,
                    "title": "Justice AI Access",
                    "description": description,
                    "payload": payload,
                    "provider_token": "",
                    "currency": "XTR",
                    "prices": [{"label": description, "amount": plan["stars"]}],
                }, timeout=20)

            elif data == "ask_question":
                if not state.get("case_text"):
                    send_message(chat_id, TEXT[lang]["no_case"])
                else:
                    state["awaiting_followup"] = True
                    send_message(chat_id, TEXT[lang]["ask_prompt"])

            elif data == "show_docs":
                if not state.get("case_text"):
                    send_message(chat_id, TEXT[lang]["no_case"])
                else:
                    send_message(chat_id, TEXT[lang]["choose_doc"], reply_markup=doc_menu(lang, state.get("case_text") or "", state.get("country")))

            elif data.startswith("doc_"):
                if not state.get("case_text"):
                    send_message(chat_id, TEXT[lang]["no_case"])
                else:
                    doc_type = data.replace("doc_", "")
                    send_message(chat_id, TEXT[lang]["doc_thinking"])
                    full_text = f"Bylos numeris: {state.get('case_number')}\n\n{state.get('case_text')}"
                    answer = openai_request(build_doc_prompt(full_text, lang, state.get("country") or "lt", doc_type), lang)
                    send_chunks(chat_id, answer)

            return jsonify({"ok": True})

        if "pre_checkout_query" in update:
            pcq = update["pre_checkout_query"]
            requests.post(f"{TELEGRAM_API}/answerPreCheckoutQuery", json={"pre_checkout_query_id": pcq.get("id"), "ok": True}, timeout=10)
            return jsonify({"ok": True})

        if "message" in update:
            msg = update["message"]
            chat_id = msg.get("chat", {}).get("id")
            user = msg.get("from", {})
            text = msg.get("text", "")
            if not chat_id:
                return jsonify({"ok": True})
            state = get_state(chat_id)

            if text.startswith("/start"):
                show_start(chat_id, user)
                return jsonify({"ok": True})

            if msg.get("successful_payment"):
                payload = msg["successful_payment"].get("invoice_payload", "")
                if payload.startswith("sub|"):
                    _, plan_key, _ = payload.split("|")
                    plan = PLANS.get(plan_key)
                    if plan:
                        lang = state.get("lang") or detect_lang(user)
                        until = db_set_subscription(chat_id, plan_key, plan["months"], plan["stars"])
                        send_message(chat_id, f"{TEXT[lang]['access_active']}\n{TEXT[lang]['active_until']} {until.date()}")
                return jsonify({"ok": True})

            if msg.get("document") or msg.get("photo"):
                if not state.get("accepted"):
                    show_start(chat_id, user)
                elif not state.get("country"):
                    show_country(chat_id)
                else:
                    handle_file(chat_id, msg)
                return jsonify({"ok": True})

            lang = state.get("lang") or detect_lang(user)
            if not state.get("accepted"):
                show_start(chat_id, user)
            elif not state.get("country"):
                show_country(chat_id)
            elif state.get("awaiting_followup") and state.get("case_text"):
                state["awaiting_followup"] = False
                send_message(chat_id, TEXT[lang]["thinking"])
                answer = openai_request(build_followup_prompt(state["case_text"], text, lang, state.get("country") or "lt"), lang)
                send_chunks(chat_id, answer, reply_markup=after_full_menu(lang))
            else:
                create_case_from_text(chat_id, text)

            return jsonify({"ok": True})

    except Exception as e:
        logger.exception("Webhook error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})


@app.route("/set-webhook", methods=["GET"])
def set_webhook():
    if not PUBLIC_BASE_URL:
        return jsonify({"ok": False, "error": "PUBLIC_BASE_URL or RENDER_EXTERNAL_URL missing"}), 400
    webhook_url = PUBLIC_BASE_URL.rstrip("/") + "/telegram-webhook"
    r = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": webhook_url}, timeout=20)
    return jsonify(r.json())


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
