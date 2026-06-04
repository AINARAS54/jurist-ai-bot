import os
import asyncio
from io import BytesIO
from datetime import datetime, timezone, timedelta

from flask import Flask, request
from openai import OpenAI
from supabase import create_client, Client

from pypdf import PdfReader
from docx import Document

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
    ContextTypes,
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://jurist-ai-bot.onrender.com")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY")
if not SUPABASE_URL:
    raise RuntimeError("Missing SUPABASE_URL")
if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

web_app = Flask(__name__)
users = {}

PLANS = {
    "1m": {"months": 1, "stars": 100, "label_lt": "1 mėnuo", "label_en": "1 month", "label_no": "1 måned"},
    "3m": {"months": 3, "stars": 250, "label_lt": "3 mėnesiai", "label_en": "3 months", "label_no": "3 måneder"},
    "6m": {"months": 6, "stars": 450, "label_lt": "6 mėnesiai", "label_en": "6 months", "label_no": "6 måneder"},
    "12m": {"months": 12, "stars": 800, "label_lt": "12 mėnesių", "label_en": "12 months", "label_no": "12 måneder"},
}

SYSTEM_PROMPT = """
You are Justice AI - Consumer Rights & Legal Claims Assistant.

Language:
- Reply only in the user's selected language.
- Never mix languages.

Legal logic:
- Apply the selected jurisdiction/country to the case.
- Always include relevant law names and article/section numbers when possible.
- Never state that a crime definitely occurred.
- Use cautious wording: potentially applicable, may be relevant, may be assessed under.

Style:
- Use grammatically correct, natural and professional language.
- Avoid repetition.
- Use clear complete sentences.
- Keep answers practical and structured.
- In Lithuanian, avoid the word "normaliai" in legal explanations; use "įprastai", "pagal paskirtį" or "įprastomis naudojimo sąlygomis".

Rules:
- Do not claim to be a lawyer.
- Do not suggest consulting lawyers or external specialists.
- Provide practical next steps yourself.
- Mention institutions only as concrete action targets.
- Separate facts from assumptions.

Documents:
- Do not use greetings such as Gerbiamasis, Gerbiamoji, Gerb. pone, Gerb. ponia, Dear Sir/Madam.
- Start documents with the recipient name and document title.
- Do not write [Paraiška pridedama].
- If attachments are relevant, use a separate section named Priedai / Attachments / Vedlegg.
"""

WELCOME_TEXT = {
    "lt": """⚖️ JUSTICE AI

Vartotojų teisių apsaugos ir teisinių pretenzijų sistema""",
    "en": """⚖️ JUSTICE AI

Consumer Rights & Legal Claims Assistant""",
    "no": """⚖️ JUSTICE AI

Assistent for forbrukerrettigheter og juridiske krav""",
}

SAFETY_TEXT = {
    "lt": """🔐 Saugumas ir konfidencialumas

Naudojamos platformos:

🤖 OpenAI
☁️ Supabase
🌐 Render
✈️ Telegram

• Duomenys saugomi ES infrastruktūroje.
• Duomenys saugomi iki prenumeratos pabaigos.
• Pasibaigus prenumeratai, duomenys ištrinami.""",
    "en": """🔐 Security and confidentiality

Platforms used:

🤖 OpenAI
☁️ Supabase
🌐 Render
✈️ Telegram

• Data is stored in EU infrastructure.
• Data is stored until the subscription ends.
• After the subscription ends, data is deleted.""",
    "no": """🔐 Sikkerhet og konfidensialitet

Plattformer som brukes:

🤖 OpenAI
☁️ Supabase
🌐 Render
✈️ Telegram

• Data lagres i EU-infrastruktur.
• Data lagres til abonnementet utløper.
• Etter at abonnementet utløper, slettes data.""",
}

LANG_TEXT = {
    "lt": {
        "accept": "✅ Suprantu ir sutinku",
        "choose_country": "🌍 Pasirinkite šalį:",
        "describe": "📋 Surinkime duomenis bylai\n\nTrumpai aprašykite situaciją arba įkelkite dokumentą.\n\nPavyzdžiai:\n\n• Negavau prekės\n• Pardavėjas negrąžina pinigų\n• Įtariamas sukčiavimas\n• Problema su prenumerata\n• Garantinis ginčas\n\nKuo daugiau informacijos pateiksite, tuo tikslesnė bus analizė.",
        "case_created": "📁 Byla sukurta",
        "thinking": "⏳ Ruošiu atsakymą...",
        "free_done": "🔎 Tai pirminis bylos vertinimas.\n\nNorint gauti išsamų vertinimą, veiksmų planą ir dokumentų projektus, pasirinkite prieigos laikotarpį.",
        "paid": "✅ Prieiga aktyvuota.",
        "test": "🧪 TEST REŽIMAS: prieiga aktyvuota.",
        "after_full": "❓ Norite patikslinti bylą?\n\nGalite užduoti papildomą klausimą arba sugeneruoti dokumentą pagal šią bylą.",
        "ask_prompt": "❓ Parašykite papildomą klausimą dėl šios bylos.",
        "choose_doc": "📄 Pasirinkite, kokį dokumentą norite sugeneruoti:",
        "no_case": "Pirmiausia aprašykite situaciją arba įkelkite dokumentą.",
        "doc_thinking": "⏳ Ruošiu dokumentą...",
        "file_received": "📎 Failas gautas. Bandau nuskaityti turinį ir pridėti prie bylos...",
        "file_saved": "✅ Failas pridėtas prie bylos.",
        "file_no_text": "⚠️ Failą išsaugojau, bet teksto nuskaityti nepavyko. Galite trumpai aprašyti, kas jame svarbu.",
        "active_until": "✅ Prieiga aktyvi iki:",
        "choose_plan": "🔓 Pasirinkite prieigos laikotarpį:",
    },
    "en": {
        "accept": "✅ I understand and agree",
        "choose_country": "🌍 Choose country:",
        "describe": "📋 Let us collect case details\n\nBriefly describe the situation or upload a document.\n\nExamples:\n\n• I did not receive my order\n• Seller refuses to refund me\n• Possible fraud or scam\n• Subscription problem\n• Warranty dispute\n\nThe more information you provide, the more accurate the review will be.",
        "case_created": "📁 Case created",
        "thinking": "⏳ Preparing response...",
        "free_done": "🔎 This is an initial case assessment.\n\nTo receive a full review, action plan and document drafts, choose an access period.",
        "paid": "✅ Access activated.",
        "test": "🧪 TEST MODE: access activated.",
        "after_full": "❓ Would you like to clarify the case?\n\nYou can ask a follow-up question or generate a document based on this case.",
        "ask_prompt": "❓ Write your follow-up question about this case.",
        "choose_doc": "📄 Choose which document you want to generate:",
        "no_case": "Please describe your situation or upload a document first.",
        "doc_thinking": "⏳ Preparing document...",
        "file_received": "📎 File received. I will try to read it and add it to the case...",
        "file_saved": "✅ File added to the case.",
        "file_no_text": "⚠️ I saved the file, but could not extract readable text. Please briefly describe what is important in it.",
        "active_until": "✅ Access active until:",
        "choose_plan": "🔓 Choose access period:",
    },
    "no": {
        "accept": "✅ Jeg forstår og samtykker",
        "choose_country": "🌍 Velg land:",
        "describe": "📋 La oss samle saksinformasjon\n\nBeskriv kort situasjonen eller last opp et dokument.\n\nEksempler:\n\n• Jeg mottok ikke varen\n• Selger nekter refusjon\n• Mulig svindel\n• Problem med abonnement\n• Garantitvist\n\nJo mer informasjon du gir, desto mer presis blir vurderingen.",
        "case_created": "📁 Sak opprettet",
        "thinking": "⏳ Forbereder svar...",
        "free_done": "🔎 Dette er en første vurdering av saken.\n\nFor full vurdering, handlingsplan og dokumentutkast, velg tilgangsperiode.",
        "paid": "✅ Tilgang aktivert.",
        "test": "🧪 TESTMODUS: tilgang aktivert.",
        "after_full": "❓ Vil du avklare saken videre?\n\nDu kan stille et oppfølgingsspørsmål eller generere et dokument basert på saken.",
        "ask_prompt": "❓ Skriv oppfølgingsspørsmålet ditt om denne saken.",
        "choose_doc": "📄 Velg hvilket dokument du vil generere:",
        "no_case": "Vennligst beskriv situasjonen din eller last opp et dokument først.",
        "doc_thinking": "⏳ Forbereder dokument...",
        "file_received": "📎 Fil mottatt. Jeg prøver å lese innholdet og legge det til saken...",
        "file_saved": "✅ Fil lagt til saken.",
        "file_no_text": "⚠️ Filen er lagret, men tekst kunne ikke leses. Beskriv kort hva som er viktig i den.",
        "active_until": "✅ Tilgang aktiv til:",
        "choose_plan": "🔓 Velg tilgangsperiode:",
    },
}

JURISDICTIONS = {
    "lt": {"lt": "Lietuva", "en": "Lithuania", "no": "Litauen"},
    "no": {"lt": "Norvegija", "en": "Norway", "no": "Norge"},
    "eu": {"lt": "ES", "en": "EU", "no": "EU"},
    "uk": {"lt": "Jungtinė Karalystė", "en": "United Kingdom", "no": "Storbritannia"},
}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def add_months(months):
    return now_utc() + timedelta(days=30 * months)


def detect_language_from_telegram(telegram_user):
    code = (getattr(telegram_user, "language_code", None) or "").lower()
    if code.startswith("lt"):
        return "lt"
    if code.startswith("no") or code.startswith("nb") or code.startswith("nn"):
        return "no"
    return "en"


def get_user_state(user_id):
    if user_id not in users:
        users[user_id] = {
            "lang": None,
            "jurisdiction": None,
            "case_text": None,
            "case_id": None,
            "case_number": None,
            "unlocked": False,
            "last_answer": None,
            "awaiting_followup": False,
        }
    return users[user_id]


def lang_name(lang):
    if lang == "lt":
        return "Lithuanian"
    if lang == "no":
        return "Norwegian"
    return "English"


def jurisdiction_name(jurisdiction, lang):
    return JURISDICTIONS.get(jurisdiction, JURISDICTIONS["lt"]).get(lang, JURISDICTIONS[jurisdiction].get("en", "Lithuania"))


def db_user_get(telegram_id):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if res.data:
        return res.data[0]
    return None


def db_user_upsert(telegram_user, lang=None):
    data = {
        "telegram_id": telegram_user.id,
        "username": telegram_user.username,
    }
    if lang:
        data["language"] = lang

    existing = db_user_get(telegram_user.id)
    if existing:
        supabase.table("users").update(data).eq("telegram_id", telegram_user.id).execute()
    else:
        data["language"] = lang or "lt"
        supabase.table("users").insert(data).execute()


def db_set_subscription(telegram_id, plan_name, months, stars):
    until = add_months(months)
    supabase.table("users").update({
        "subscription_until": iso(until),
        "plan_name": plan_name,
    }).eq("telegram_id", telegram_id).execute()

    supabase.table("payments").insert({
        "telegram_id": telegram_id,
        "plan_name": plan_name,
        "stars_paid": stars,
        "valid_until": iso(until),
    }).execute()

    return until


def db_subscription_active(telegram_id):
    user = db_user_get(telegram_id)
    if not user:
        return False, None

    until = parse_dt(user.get("subscription_until"))
    if not until:
        return False, None

    return until > now_utc(), until


def db_create_case(telegram_id, case_text):
    title = case_text[:80] if case_text else "New case"

    res = supabase.table("cases").insert({
        "telegram_id": telegram_id,
        "title": title,
        "case_text": case_text,
        "status": "open",
    }).execute()

    case = res.data[0]
    case_id = case["id"]
    case_number = f"CASE-{now_utc().year}-{case_id:06d}"

    supabase.table("cases").update({"case_number": case_number}).eq("id", case_id).execute()
    return case_id, case_number


def db_update_case(case_id, case_text):
    if not case_id:
        return
    supabase.table("cases").update({"case_text": case_text}).eq("id", case_id).execute()


def db_add_case_file(case_id, file_name, file_type, telegram_file_id):
    if not case_id:
        return
    supabase.table("case_files").insert({
        "case_id": case_id,
        "file_name": file_name,
        "file_type": file_type,
        "telegram_file_id": telegram_file_id,
    }).execute()


def document_name(doc_type, lang):
    names = {
        "lt": {
            "seller_claim": "pretenziją pardavėjui",
            "chargeback": "prašymą bankui dėl pinigų grąžinimo",
            "authority_complaint": "skundą institucijai",
            "fraud_report": "pareiškimą dėl galimo sukčiavimo",
        },
        "en": {
            "seller_claim": "seller complaint letter",
            "chargeback": "bank refund request",
            "authority_complaint": "consumer authority complaint",
            "fraud_report": "fraud report",
        },
        "no": {
            "seller_claim": "klagebrev til selger",
            "chargeback": "forespørsel til banken om tilbakebetaling",
            "authority_complaint": "klage til forbrukermyndighet",
            "fraud_report": "svindelrapport",
        },
    }
    return names.get(lang, names["en"]).get(doc_type, "document")


def legal_sources_instruction(lang, jurisdiction):
    j = jurisdiction_name(jurisdiction, lang)
    if lang == "lt":
        return f"""
Taikoma šalis: {j}.
Visada pridėk skyrių "⚖️ Galimai taikytini teisės aktai".
Nurodyk įstatymo pavadinimą, straipsnio arba paragrafo numerį ir trumpą ryšį su byla.
Jei galimas sukčiavimas ar kita nusikalstama veika, rašyk tik atsargiai: "gali būti aktualu", "galimai taikytina", "gali būti vertinama pagal".
Niekada neteigk, kad nusikaltimas tikrai įvykdytas.
"""
    if lang == "no":
        return f"""
Valgt land: {j}.
Ta alltid med delen "⚖️ Mulig relevante lover".
Oppgi lovnavn, paragraf/section og kort forklaring.
Ved mulig svindel, bruk forsiktige formuleringer som "kan være relevant" eller "kan vurderes etter".
Ikke si at en straffbar handling definitivt har skjedd.
"""
    return f"""
Selected jurisdiction: {j}.
Always include a section "⚖️ Potentially applicable laws".
List law names, article/section numbers and a short link to the case.
For possible fraud or crime, use cautious wording such as "may be relevant" or "may be assessed under".
Never state that a crime definitely occurred.
"""


def build_task(user_text, lang, jurisdiction, full=False):
    legal = legal_sources_instruction(lang, jurisdiction)

    if lang == "lt":
        if full:
            return f"""
Atsakyk tik lietuviškai.
{legal}
Paruošk pilną bylos vertinimą:

📁 Bylos numeris
📋 Situacijos santrauka
📌 Pagrindiniai faktai
⚖️ Galimai taikytini teisės aktai
⚠️ Galimi pažeidimai
📂 Reikalingi įrodymai
📊 Bylos stiprumas 0-100
🎯 Veiksmų planas
📄 Galimi dokumentai
💳 Galimybė susigrąžinti pinigus per banką
✅ Išvada

🧭 Klausimai bylai patikslinti
Pateik ne daugiau kaip 2 svarbiausius klausimus. Jei klausimai nebūtini, šio skyriaus nerodyk.

💡 Praktiniai pasiūlymai
Pateik ne daugiau kaip 2 svarbiausius pasiūlymus.

Nesiūlyk kreiptis į konsultantus ar teisininkus. Pateik konkrečius veiksmus pats.
Venk žodžio "normaliai". Naudok "įprastai", "pagal paskirtį" arba "įprastomis naudojimo sąlygomis".

Bylos informacija:
{user_text}
"""
        return f"""
Atsakyk tik lietuviškai.
{legal}
Paruošk trumpą pirminį vertinimą:

📋 Kategorija
⚠️ Pagrindinė problema
⚖️ Galimai taikytini teisės aktai
📂 Trūkstami įrodymai
🎯 Pirmas rekomenduojamas veiksmas
🔓 Išplėstinio atsakymo galimybė

Nesiūlyk kreiptis į konsultantus ar teisininkus. Venk žodžio "normaliai".

Situacija:
{user_text}
"""

    if lang == "no":
        if full:
            return f"""
Svar kun på norsk.
{legal}
Lag en full vurdering:

📁 Saksnummer
📋 Situasjonsoppsummering
📌 Viktige fakta
⚖️ Mulig relevante lover
⚠️ Mulige brudd
📂 Nødvendige bevis
📊 Sakens styrke 0-100
🎯 Handlingsplan
📄 Mulige dokumenter
💳 Mulighet for tilbakebetaling via bank
✅ Konklusjon

🧭 Maks 2 oppfølgingsspørsmål
💡 Maks 2 praktiske forslag

Ikke anbefal eksterne rådgivere eller advokater. Gi konkrete tiltak selv.

Saksinformasjon:
{user_text}
"""
        return f"""
Svar kun på norsk.
{legal}
Lag en kort førstevurdering:

📋 Kategori
⚠️ Hovedproblem
⚖️ Mulig relevante lover
📂 Manglende bevis
🎯 Første anbefalte steg
🔓 Mulighet for utvidet svar

Ikke anbefal eksterne rådgivere eller advokater.

Situasjon:
{user_text}
"""

    if full:
        return f"""
Answer only in English.
{legal}
Prepare a full case review:

📁 Case number
📋 Situation summary
📌 Key facts
⚖️ Potentially applicable laws
⚠️ Possible violations
📂 Evidence needed
📊 Case strength 0-100
🎯 Action plan
📄 Possible documents
💳 Bank refund option
✅ Conclusion

🧭 Max 2 follow-up questions
💡 Max 2 practical suggestions

Do not suggest external lawyers or advisors. Provide concrete next steps yourself.

Case information:
{user_text}
"""
    return f"""
Answer only in English.
{legal}
Prepare a short initial assessment:

📋 Likely category
⚠️ Main issue
⚖️ Potentially applicable laws
📂 Missing evidence
🎯 First recommended step
🔓 Extended response option

Do not suggest external lawyers or advisors.

User situation:
{user_text}
"""


def build_document_task(user_text, lang, doc_type, jurisdiction):
    doc = document_name(doc_type, lang)
    legal = legal_sources_instruction(lang, jurisdiction)

    if lang == "lt":
        return f"""
Atsakyk tik lietuviškai.
{legal}
Paruošk dokumentą: {doc}.

Reikalavimai:
- Dokumentą pradėk nuo gavėjo pavadinimo ir dokumento antraštės.
- Nenaudok kreipinių: Gerbiamasis, Gerbiamoji, Gerb. pone, Gerb. ponia.
- Oficialus, gramatiškai taisyklingas tekstas.
- Paruošta siųsti.
- Naudok laukus: [Vardas Pavardė], [Adresas], [El. paštas], [Telefonas], [Data].
- Jei trūksta duomenų, naudok laužtinius skliaustus.
- Nerašyk [Paraiška pridedama].
- Jei yra priedų, naudok atskirą skyrių "Priedai:". Jei priedų nėra, šio skyriaus nerodyk.
- Nesiūlyk kreiptis į konsultantus ar teisininkus.
- Venk žodžio "normaliai". Naudok "įprastai", "pagal paskirtį" arba "įprastomis naudojimo sąlygomis".

Bylos informacija:
{user_text}
"""

    if lang == "no":
        return f"""
Svar kun på norsk.
{legal}
Lag dokumentet: {doc}.

Krav:
- Start med mottakerens navn og dokumenttittel.
- Ikke bruk generiske hilsener.
- Formelt og grammatisk korrekt.
- Klart til sending.
- Bruk felter: [Navn], [Adresse], [E-post], [Telefon], [Dato].
- Hvis informasjon mangler, bruk hakeparenteser.
- Hvis vedlegg er relevante, bruk en egen del "Vedlegg:". Hvis ikke, ikke vis den delen.
- Ikke anbefal eksterne rådgivere eller advokater.

Saksinformasjon:
{user_text}
"""

    return f"""
Answer only in English.
{legal}
Prepare this document: {doc}.

Requirements:
- Start with recipient name and document title.
- Do not use greetings such as Dear Sir/Madam.
- Formal and grammatically correct.
- Ready to send.
- Use placeholders: [Full name], [Address], [Email], [Phone], [Date].
- If information is missing, use square brackets.
- If attachments are relevant, use a separate section "Attachments:". If not, do not show it.
- Do not suggest external lawyers or advisors.

Case information:
{user_text}
"""


def build_followup_task(case_text, question, lang, jurisdiction):
    legal = legal_sources_instruction(lang, jurisdiction)
    if lang == "lt":
        return f"""
Atsakyk tik lietuviškai.
{legal}
Atsakyk į vartotojo papildomą klausimą pagal šią bylą.
Būk konkretus, aiškus ir praktiškas. Nesiūlyk kreiptis į konsultantus ar teisininkus.

Bylos informacija:
{case_text}

Klausimas:
{question}
"""
    if lang == "no":
        return f"""
Svar kun på norsk.
{legal}
Svar på brukerens oppfølgingsspørsmål basert på saken.
Vær konkret, tydelig og praktisk. Ikke anbefal eksterne rådgivere eller advokater.

Saksinformasjon:
{case_text}

Spørsmål:
{question}
"""
    return f"""
Answer only in English.
{legal}
Answer the user's follow-up question based on the case.
Be clear, concrete and practical. Do not suggest external lawyers or advisors.

Case information:
{case_text}

Question:
{question}
"""


async def ask_openai_task(task, lang):
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT + f"\nCurrent selected language: {lang_name(lang)}. Reply only in {lang_name(lang)}.",
            },
            {"role": "user", "content": task},
        ],
    )
    return response.choices[0].message.content


async def ask_openai(user_text, lang, jurisdiction, full=False):
    return await ask_openai_task(build_task(user_text, lang, jurisdiction, full), lang)


async def ask_openai_document(user_text, lang, doc_type, jurisdiction):
    return await ask_openai_task(build_document_task(user_text, lang, doc_type, jurisdiction), lang)


async def ask_openai_followup(case_text, question, lang, jurisdiction):
    return await ask_openai_task(build_followup_task(case_text, question, lang, jurisdiction), lang)


def extract_text_from_file(file_name, file_bytes):
    lower = file_name.lower()
    try:
        if lower.endswith(".txt"):
            return file_bytes.decode("utf-8", errors="ignore")
        if lower.endswith(".pdf"):
            reader = PdfReader(BytesIO(file_bytes))
            text = []
            for page in reader.pages:
                text.append(page.extract_text() or "")
            return "\n".join(text).strip()
        if lower.endswith(".docx"):
            doc = Document(BytesIO(file_bytes))
            return "\n".join(p.text for p in doc.paragraphs).strip()
        return ""
    except Exception:
        return ""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = detect_language_from_telegram(update.effective_user)

    users[user_id] = {
        "lang": lang,
        "jurisdiction": None,
        "case_text": None,
        "case_id": None,
        "case_number": None,
        "unlocked": False,
        "last_answer": None,
        "awaiting_followup": False,
    }

    db_user_upsert(update.effective_user, lang)

    keyboard = [[InlineKeyboardButton(LANG_TEXT[lang]["accept"], callback_data="accept_safety")]]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{WELCOME_TEXT[lang]}\n\n{SAFETY_TEXT[lang]}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚖️ Justice AI. Use /start to begin.")


async def accept_safety_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = get_user_state(query.from_user.id)
    lang = user.get("lang") or detect_language_from_telegram(query.from_user)
    user["lang"] = lang

    keyboard = [
        [InlineKeyboardButton("🇱🇹 Lietuva", callback_data="jur_lt")],
        [InlineKeyboardButton("🇳🇴 Norvegija", callback_data="jur_no")],
        [InlineKeyboardButton("🇪🇺 ES", callback_data="jur_eu")],
        [InlineKeyboardButton("🇬🇧 Jungtinė Karalystė", callback_data="jur_uk")],
    ]

    await query.edit_message_text(
        LANG_TEXT[lang]["choose_country"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def jurisdiction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = get_user_state(query.from_user.id)
    lang = user.get("lang") or detect_language_from_telegram(query.from_user)
    jurisdiction = query.data.replace("jur_", "")

    user["lang"] = lang
    user["jurisdiction"] = jurisdiction

    await query.edit_message_text(LANG_TEXT[lang]["describe"])


async def send_plan_menu(message, lang):
    if lang == "lt":
        labels = {"1m": "⭐ 100 - 1 mėnuo", "3m": "⭐ 250 - 3 mėnesiai", "6m": "⭐ 450 - 6 mėnesiai", "12m": "⭐ 800 - 12 mėnesių"}
    elif lang == "no":
        labels = {"1m": "⭐ 100 - 1 måned", "3m": "⭐ 250 - 3 måneder", "6m": "⭐ 450 - 6 måneder", "12m": "⭐ 800 - 12 måneder"}
    else:
        labels = {"1m": "⭐ 100 - 1 month", "3m": "⭐ 250 - 3 months", "6m": "⭐ 450 - 6 months", "12m": "⭐ 800 - 12 months"}

    keyboard = [[InlineKeyboardButton(label, callback_data=f"plan_{key}")] for key, label in labels.items()]
    await message.reply_text(LANG_TEXT[lang]["choose_plan"], reply_markup=InlineKeyboardMarkup(keyboard))


async def send_after_full_menu(message, lang):
    if lang == "lt":
        keyboard = [[InlineKeyboardButton("❓ Užduoti klausimą", callback_data="ask_question")], [InlineKeyboardButton("📄 Generuoti dokumentą", callback_data="show_docs")]]
    elif lang == "no":
        keyboard = [[InlineKeyboardButton("❓ Still spørsmål", callback_data="ask_question")], [InlineKeyboardButton("📄 Generer dokument", callback_data="show_docs")]]
    else:
        keyboard = [[InlineKeyboardButton("❓ Ask a question", callback_data="ask_question")], [InlineKeyboardButton("📄 Generate document", callback_data="show_docs")]]

    await message.reply_text(LANG_TEXT[lang]["after_full"], reply_markup=InlineKeyboardMarkup(keyboard))


async def send_document_menu(message, lang):
    if lang == "lt":
        keyboard = [
            [InlineKeyboardButton("📄 Pretenzija pardavėjui", callback_data="doc_seller_claim")],
            [InlineKeyboardButton("💳 Prašymas bankui dėl pinigų grąžinimo", callback_data="doc_chargeback")],
            [InlineKeyboardButton("⚖️ Skundas institucijai", callback_data="doc_authority_complaint")],
            [InlineKeyboardButton("🚨 Pareiškimas dėl sukčiavimo", callback_data="doc_fraud_report")],
        ]
    elif lang == "no":
        keyboard = [
            [InlineKeyboardButton("📄 Klagebrev til selger", callback_data="doc_seller_claim")],
            [InlineKeyboardButton("💳 Tilbakebetaling via bank", callback_data="doc_chargeback")],
            [InlineKeyboardButton("⚖️ Klage til myndighet", callback_data="doc_authority_complaint")],
            [InlineKeyboardButton("🚨 Svindelrapport", callback_data="doc_fraud_report")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("📄 Seller complaint letter", callback_data="doc_seller_claim")],
            [InlineKeyboardButton("💳 Bank refund request", callback_data="doc_chargeback")],
            [InlineKeyboardButton("⚖️ Consumer authority complaint", callback_data="doc_authority_complaint")],
            [InlineKeyboardButton("🚨 Fraud report", callback_data="doc_fraud_report")],
        ]
    await message.reply_text(LANG_TEXT[lang]["choose_doc"], reply_markup=InlineKeyboardMarkup(keyboard))


def ensure_jurisdiction(user):
    if not user.get("jurisdiction"):
        user["jurisdiction"] = "lt"
    return user["jurisdiction"]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_user_state(user_id)

    if not user.get("lang"):
        await start(update, context)
        return

    lang = user["lang"]
    jurisdiction = ensure_jurisdiction(user)

    if user.get("awaiting_followup") and user.get("case_text"):
        user["awaiting_followup"] = False
        await update.message.reply_text(LANG_TEXT[lang]["thinking"])
        answer = await ask_openai_followup(user["case_text"], update.message.text, lang, jurisdiction)
        for i in range(0, len(answer), 3900):
            await update.message.reply_text(answer[i:i + 3900])
        await send_after_full_menu(update.message, lang)
        return

    user["case_text"] = update.message.text
    user["awaiting_followup"] = False

    case_id, case_number = db_create_case(user_id, user["case_text"])
    user["case_id"] = case_id
    user["case_number"] = case_number

    await update.message.reply_text(f"{LANG_TEXT[lang]['case_created']}\n\n🆔 Bylos Nr.: {case_number}")

    active, until = db_subscription_active(user_id)

    if active:
        user["unlocked"] = True
        await update.message.reply_text(f"{LANG_TEXT[lang]['active_until']} {until.date()}")
        await update.message.reply_text(LANG_TEXT[lang]["thinking"])
        full_text = f"Bylos numeris: {case_number}\nTaikoma šalis: {jurisdiction_name(jurisdiction, lang)}\n\n{user['case_text']}"
        answer = await ask_openai(full_text, lang, jurisdiction, full=True)
        for i in range(0, len(answer), 3900):
            await update.message.reply_text(answer[i:i + 3900])
        await send_after_full_menu(update.message, lang)
        return

    await update.message.reply_text(LANG_TEXT[lang]["thinking"])
    preview_text = f"Bylos numeris: {case_number}\nTaikoma šalis: {jurisdiction_name(jurisdiction, lang)}\n\n{user['case_text']}"
    answer = await ask_openai(preview_text, lang, jurisdiction, full=False)
    user["last_answer"] = answer
    await update.message.reply_text(answer[:3900])
    await update.message.reply_text(LANG_TEXT[lang]["free_done"])
    await send_plan_menu(update.message, lang)


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_user_state(user_id)

    if not user.get("lang"):
        await start(update, context)
        return

    lang = user["lang"]
    jurisdiction = ensure_jurisdiction(user)
    await update.message.reply_text(LANG_TEXT[lang]["file_received"])

    file_name = "uploaded_file"
    file_id = None
    file_type = "unknown"

    if update.message.document:
        doc = update.message.document
        file_id = doc.file_id
        file_name = doc.file_name or "document"
        file_type = doc.mime_type or "document"
    elif update.message.photo:
        photo = update.message.photo[-1]
        file_id = photo.file_id
        file_name = "photo.jpg"
        file_type = "image/jpeg"

    if not file_id:
        await update.message.reply_text(LANG_TEXT[lang]["file_no_text"])
        return

    tg_file = await context.bot.get_file(file_id)
    file_bytes = bytes(await tg_file.download_as_bytearray())
    extracted = extract_text_from_file(file_name, file_bytes)

    if not user.get("case_id"):
        base_text = f"Įkelta byla iš failo: {file_name}" if lang == "lt" else f"Case created from file: {file_name}"
        case_id, case_number = db_create_case(user_id, base_text)
        user["case_id"] = case_id
        user["case_number"] = case_number
        user["case_text"] = base_text
        await update.message.reply_text(f"{LANG_TEXT[lang]['case_created']}\n\n🆔 Bylos Nr.: {case_number}")

    db_add_case_file(user["case_id"], file_name, file_type, file_id)

    if extracted:
        user["case_text"] = (user.get("case_text") or "") + f"\n\n--- FILE: {file_name} ---\n{extracted[:6000]}"
        db_update_case(user["case_id"], user["case_text"])
        await update.message.reply_text(LANG_TEXT[lang]["file_saved"])
    else:
        await update.message.reply_text(LANG_TEXT[lang]["file_no_text"])

    active, _ = db_subscription_active(user_id)
    if active:
        user["unlocked"] = True
        await update.message.reply_text(LANG_TEXT[lang]["thinking"])
        full_text = f"Bylos numeris: {user.get('case_number') or 'Nenurodytas'}\nTaikoma šalis: {jurisdiction_name(jurisdiction, lang)}\n\n{user['case_text']}"
        answer = await ask_openai(full_text, lang, jurisdiction, full=True)
        for i in range(0, len(answer), 3900):
            await update.message.reply_text(answer[i:i + 3900])
        await send_after_full_menu(update.message, lang)
    else:
        await update.message.reply_text(LANG_TEXT[lang]["free_done"])
        await send_plan_menu(update.message, lang)


async def plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user_state(query.from_user.id)
    lang = user.get("lang") or detect_language_from_telegram(query.from_user)
    jurisdiction = ensure_jurisdiction(user)

    plan_key = query.data.replace("plan_", "")
    plan = PLANS.get(plan_key)
    if not plan:
        return

    if TEST_MODE:
        until = db_set_subscription(query.from_user.id, plan_key, plan["months"], plan["stars"])
        user["unlocked"] = True
        await query.message.reply_text(f"{LANG_TEXT[lang]['test']}\n{LANG_TEXT[lang]['active_until']} {until.date()}")

        if user.get("case_text"):
            await query.message.reply_text(LANG_TEXT[lang]["thinking"])
            full_text = f"Bylos numeris: {user.get('case_number') or 'Nenurodytas'}\nTaikoma šalis: {jurisdiction_name(jurisdiction, lang)}\n\n{user['case_text']}"
            answer = await ask_openai(full_text, lang, jurisdiction, full=True)
            for i in range(0, len(answer), 3900):
                await query.message.reply_text(answer[i:i + 3900])
            await send_after_full_menu(query.message, lang)
        return

    title = "Justice AI Access"
    description = f"{plan['months']} month access"
    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title=title,
        description=description,
        payload=f"sub|{plan_key}|{query.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(description, plan["stars"])],
    )


async def ask_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user_state(query.from_user.id)
    lang = user.get("lang") or detect_language_from_telegram(query.from_user)
    if not user.get("case_text"):
        await query.message.reply_text(LANG_TEXT[lang]["no_case"])
        return
    user["awaiting_followup"] = True
    await query.message.reply_text(LANG_TEXT[lang]["ask_prompt"])


async def show_docs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user_state(query.from_user.id)
    lang = user.get("lang") or detect_language_from_telegram(query.from_user)
    if not user.get("case_text"):
        await query.message.reply_text(LANG_TEXT[lang]["no_case"])
        return
    await send_document_menu(query.message, lang)


async def document_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = get_user_state(query.from_user.id)
    lang = user.get("lang") or detect_language_from_telegram(query.from_user)
    jurisdiction = ensure_jurisdiction(user)

    if not user.get("case_text"):
        await query.message.reply_text(LANG_TEXT[lang]["no_case"])
        return

    active, _ = db_subscription_active(query.from_user.id)
    if not active and not TEST_MODE:
        await query.message.reply_text(LANG_TEXT[lang]["free_done"])
        await send_plan_menu(query.message, lang)
        return

    doc_type = query.data.replace("doc_", "")
    await query.message.reply_text(LANG_TEXT[lang]["doc_thinking"])
    full_text = f"Bylos numeris: {user.get('case_number') or 'Nenurodytas'}\nTaikoma šalis: {jurisdiction_name(jurisdiction, lang)}\n\n{user['case_text']}"
    document = await ask_openai_document(full_text, lang, doc_type, jurisdiction)
    for i in range(0, len(document), 3900):
        await query.message.reply_text(document[i:i + 3900])


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    user_id = update.message.from_user.id
    user = get_user_state(user_id)
    lang = user.get("lang") or detect_language_from_telegram(update.message.from_user)
    jurisdiction = ensure_jurisdiction(user)

    if payload.startswith("sub|"):
        _, plan_key, _ = payload.split("|")
        plan = PLANS.get(plan_key)
        if plan:
            until = db_set_subscription(user_id, plan_key, plan["months"], plan["stars"])
            user["unlocked"] = True
            await update.message.reply_text(f"{LANG_TEXT[lang]['paid']}\n{LANG_TEXT[lang]['active_until']} {until.date()}")
            if user.get("case_text"):
                await update.message.reply_text(LANG_TEXT[lang]["thinking"])
                full_text = f"Bylos numeris: {user.get('case_number') or 'Nenurodytas'}\nTaikoma šalis: {jurisdiction_name(jurisdiction, lang)}\n\n{user['case_text']}"
                answer = await ask_openai(full_text, lang, jurisdiction, full=True)
                for i in range(0, len(answer), 3900):
                    await update.message.reply_text(answer[i:i + 3900])
                await send_after_full_menu(update.message, lang)


application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CallbackQueryHandler(accept_safety_callback, pattern="^accept_safety$"))
application.add_handler(CallbackQueryHandler(jurisdiction_callback, pattern="^jur_"))
application.add_handler(CallbackQueryHandler(plan_callback, pattern="^plan_"))
application.add_handler(CallbackQueryHandler(ask_question_callback, pattern="^ask_question$"))
application.add_handler(CallbackQueryHandler(show_docs_callback, pattern="^show_docs$"))
application.add_handler(CallbackQueryHandler(document_callback, pattern="^doc_"))
application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)


async def setup_bot():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=f"{RENDER_EXTERNAL_URL}/webhook")


loop.run_until_complete(setup_bot())


@web_app.get("/")
def home():
    return "Justice AI webhook bot is running."


@web_app.post("/webhook")
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    loop.run_until_complete(application.process_update(update))
    return "OK", 200
