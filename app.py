import os
import asyncio
from flask import Flask, request
from openai import OpenAI

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
UNLOCK_PRICE_STARS = int(os.getenv("UNLOCK_PRICE_STARS", "100"))
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)
web_app = Flask(__name__)
users = {}

SYSTEM_PROMPT = """
You are Jurist AI - Consumer Rights & Legal Claims Assistant.

LANGUAGE RULE:
- If language is Lithuanian, answer ONLY in Lithuanian.
- If language is English, answer ONLY in English.
- If language is Norwegian, answer ONLY in Norwegian.
- Never mix languages.
- Never use headings from another language.

IMPORTANT BEHAVIOR RULE:
- Do NOT tell the user to consult another lawyer, legal professional, consumer rights organization, advisor, or external specialist.
- Do NOT end answers with generic advice to seek outside consultation.
- You must provide the best practical next steps yourself.
- You may mention official institutions only as concrete action targets, for example: bank chargeback department, seller, payment provider, police, consumer authority complaint form.
- Avoid vague phrases like "consult a specialist" or "seek professional advice".

You help users with:
- consumer rights
- refunds
- chargebacks
- complaints
- fraud cases
- defective products
- contract disputes
- online purchases
- subscription issues
- warranty disputes
- evidence analysis

Rules:
- Do not claim to be a licensed lawyer.
- Separate facts from assumptions.
- Be clear, practical and structured.
- Do not overpromise results.
"""

WELCOME_TEXT = """⚖️ JURIST AI

Consumer Rights & Legal Claims Assistant

I can help you with:

📦 Missing or undelivered orders
💳 Chargebacks and refunds
📄 Consumer complaints
⚖️ Contract disputes
🚨 Fraud and scam cases

Select your language:"""

LANG_TEXT = {
    "lt": {
        "describe": "📁 Nauja byla\n\nTrumpai aprašykite savo situaciją.\n\nPavyzdžiai:\n\n• Negavau prekės\n• Pardavėjas negrąžina pinigų\n• Įtariamas sukčiavimas\n• Problema su prenumerata\n• Garantinis ginčas\n\nKuo daugiau detalių pateiksite, tuo tikslesnė bus išvada.",
        "thinking": "⏳ Ruošiu atsakymą...",
        "free_done": "🔎 Tai trumpas nemokamas vertinimas.\n\nNorint gauti išsamų bylos vertinimą, veiksmų planą ir dokumento juodraštį, atrakinkite išplėstinį atsakymą.",
        "unlock": "🔓 Atrakinti išplėstinį atsakymą (TEST)" if TEST_MODE else f"🔓 Atrakinti išplėstinį atsakymą - ⭐{UNLOCK_PRICE_STARS}",
        "paid": "✅ Išplėstinis atsakymas paruoštas.",
        "test": "🧪 TEST REŽIMAS: išplėstinis atsakymas atrakintas.",
        "choose_doc": "📄 Pasirinkite, kokį dokumentą norite sugeneruoti:",
        "doc_menu": "📄 Galiu paruošti atskirą dokumentą pagal šią bylą.",
        "no_case": "Pirmiausia aprašykite situaciją.",
        "doc_thinking": "⏳ Ruošiu dokumentą...",
    },
    "en": {
        "describe": "📁 New case\n\nBriefly describe your situation.\n\nExamples:\n\n• I did not receive my order\n• Seller refuses to refund me\n• Possible fraud or scam\n• Subscription problem\n• Warranty dispute\n\nThe more details you provide, the more accurate the assessment will be.",
        "thinking": "⏳ Preparing response...",
        "free_done": "🔎 This is a short free assessment.\n\nUnlock the extended response to receive a full case review, action plan and document draft.",
        "unlock": "🔓 Unlock extended response (TEST)" if TEST_MODE else f"🔓 Unlock extended response - ⭐{UNLOCK_PRICE_STARS}",
        "paid": "✅ Extended response is ready.",
        "test": "🧪 TEST MODE: extended response unlocked.",
        "choose_doc": "📄 Choose which document you want to generate:",
        "doc_menu": "📄 I can prepare a separate document based on this case.",
        "no_case": "Please describe your situation first.",
        "doc_thinking": "⏳ Preparing document...",
    },
    "no": {
        "describe": "📁 Ny sak\n\nBeskriv kort situasjonen din.\n\nEksempler:\n\n• Jeg mottok ikke varen\n• Selger nekter refusjon\n• Mulig svindel\n• Problem med abonnement\n• Garantitvist\n\nJo flere detaljer du gir, desto mer presis blir vurderingen.",
        "thinking": "⏳ Forbereder svar...",
        "free_done": "🔎 Dette er en kort gratis vurdering.\n\nLås opp utvidet svar for å få full vurdering, handlingsplan og dokumentutkast.",
        "unlock": "🔓 Lås opp utvidet svar (TEST)" if TEST_MODE else f"🔓 Lås opp utvidet svar - ⭐{UNLOCK_PRICE_STARS}",
        "paid": "✅ Utvidet svar er klart.",
        "test": "🧪 TESTMODUS: utvidet svar er låst opp.",
        "choose_doc": "📄 Velg hvilket dokument du vil generere:",
        "doc_menu": "📄 Jeg kan lage et eget dokument basert på denne saken.",
        "no_case": "Vennligst beskriv situasjonen din først.",
        "doc_thinking": "⏳ Forbereder dokument...",
    },
}


def get_user(user_id):
    if user_id not in users:
        users[user_id] = {
            "lang": None,
            "case_text": None,
            "unlocked": False,
            "last_answer": None,
        }
    return users[user_id]


def lang_name(lang):
    if lang == "lt":
        return "Lithuanian"
    if lang == "no":
        return "Norwegian"
    return "English"


def document_name(doc_type, lang):
    names = {
        "lt": {
            "seller_claim": "pretenziją pardavėjui",
            "chargeback": "chargeback prašymą bankui",
            "authority_complaint": "skundą institucijai",
        },
        "en": {
            "seller_claim": "seller complaint letter",
            "chargeback": "bank chargeback request",
            "authority_complaint": "consumer authority complaint",
        },
        "no": {
            "seller_claim": "klagebrev til selger",
            "chargeback": "chargeback-forespørsel til banken",
            "authority_complaint": "klage til forbrukermyndighet",
        },
    }
    return names.get(lang, names["en"]).get(doc_type, "document")


def build_task(user_text, lang, full=False):
    if lang == "lt":
        if full:
            return f"""
Atsakyk TIK lietuviškai. Nenaudok angliškų antraščių.

Paruošk PILNĄ Jurist AI bylos vertinimą.

Naudok šią struktūrą:

📋 Situacijos santrauka
📌 Pagrindiniai faktai
⚖️ Galimi vartotojų teisių pažeidimai
📂 Reikalingi įrodymai
📊 Bylos stiprumo balas 0-100
🎯 Rekomenduojamas veiksmų planas
📄 Galimi dokumentai
💳 Chargeback galimybė, jei aktualu
✅ Galutinė išvada

🧭 Kiti klausimai bylai patikslinti
Pabaigoje sugeneruok 3-5 konkrečius klausimus vartotojui, kurie padėtų geriau aptarnauti bylą.

💡 Papildomi pasiūlymai
Pabaigoje pateik 3 praktinius pasiūlymus, ką vartotojas turėtų padaryti toliau.

Svarbu:
- Nekartok žodžio "analizė" per dažnai.
- Nesiūlyk kreiptis į konsultantus, teisininkus ar kitus specialistus.
- Jei mini instituciją, nurodyk ją kaip konkretų veiksmą, o ne kaip bendrą konsultaciją.

Situacija:
{user_text}
"""
        return f"""
Atsakyk TIK lietuviškai. Nenaudok angliškų antraščių.

Paruošk TRUMPĄ nemokamą pirminį vertinimą.

Naudok šią struktūrą:

📋 Kategorija
⚠️ Pagrindinė problema
📂 Trūkstami įrodymai
🎯 Pirmas rekomenduojamas veiksmas
🔓 Išplėstinio atsakymo galimybė

Svarbu:
- Nekartok žodžio "analizė".
- Nesiūlyk kreiptis į konsultantus, teisininkus ar kitus specialistus.
- Pateik aiškų kitą veiksmą pats.

Situacija:
{user_text}
"""

    if lang == "no":
        if full:
            return f"""
Svar KUN på norsk. Ikke bruk engelske overskrifter.

Lag en FULL Jurist AI-vurdering.

Bruk denne strukturen:

📋 Situasjonsoppsummering
📌 Viktige fakta
⚖️ Mulige brudd på forbrukerrettigheter
📂 Nødvendige bevis
📊 Sakens styrke 0-100
🎯 Anbefalt handlingsplan
📄 Mulige dokumenter
💳 Chargeback-mulighet hvis relevant
✅ Endelig konklusjon

🧭 Oppfølgingsspørsmål for å avklare saken
Til slutt, generer 3-5 konkrete spørsmål som kan hjelpe med å håndtere saken bedre.

💡 Ekstra forslag
Til slutt, gi 3 praktiske forslag til hva brukeren bør gjøre videre.

Viktig:
- Ikke gjenta ordet "analyse" for ofte.
- Ikke anbefal å kontakte rådgivere, advokater eller eksterne spesialister.
- Hvis du nevner en institusjon, beskriv det som et konkret tiltak.

Situasjon:
{user_text}
"""
        return f"""
Svar KUN på norsk. Ikke bruk engelske overskrifter.

Lag en KORT gratis førstevurdering.

Bruk denne strukturen:

📋 Kategori
⚠️ Hovedproblem
📂 Manglende bevis
🎯 Første anbefalte steg
🔓 Mulighet for utvidet svar

Viktig:
- Ikke gjenta ordet "analyse".
- Ikke anbefal å kontakte rådgivere, advokater eller eksterne spesialister.
- Gi et tydelig neste steg selv.

Situasjon:
{user_text}
"""

    if full:
        return f"""
Answer ONLY in English. Do not use Lithuanian or Norwegian headings.

Prepare a FULL Jurist AI case review.

Use this structure:

📋 Situation summary
📌 Key facts
⚖️ Possible consumer rights violations
📂 Evidence needed
📊 Case strength score 0-100
🎯 Recommended action plan
📄 Possible documents
💳 Chargeback option if relevant
✅ Final conclusion

🧭 Follow-up questions for case clarification
At the end, generate 3-5 specific questions that would help handle the case better.

💡 Additional suggestions
At the end, provide 3 practical next-step suggestions.

Important:
- Do not repeat the word "analysis" too often.
- Do not suggest consulting lawyers, advisors, consumer organizations or external specialists.
- If you mention an institution, describe it as a concrete action target.

User situation:
{user_text}
"""
    return f"""
Answer ONLY in English. Do not use Lithuanian or Norwegian headings.

Prepare a SHORT FREE initial assessment.

Use this structure:

📋 Likely category
⚠️ Main issue
📂 Missing evidence
🎯 First recommended step
🔓 Extended response option

Important:
- Do not repeat the word "analysis".
- Do not suggest consulting lawyers, advisors, consumer organizations or external specialists.
- Provide a clear next step yourself.

User situation:
{user_text}
"""


def build_document_task(user_text, lang, doc_type):
    doc = document_name(doc_type, lang)

    if lang == "lt":
        return f"""
Atsakyk TIK lietuviškai.

Paruošk dokumentą: {doc}.

Reikalavimai:
- Dokumentas turi būti formalus, aiškus ir paruoštas kopijavimui.
- Įtrauk vietas, kur vartotojas turi įrašyti savo duomenis: [Vardas Pavardė], [Adresas], [El. paštas], [Telefonas], [Data].
- Nesiūlyk kreiptis į konsultantus, teisininkus ar kitus specialistus.
- Jei trūksta duomenų, naudok laužtinius skliaustus.
- Dokumento gale pridėk priedų sąrašą.

Bylos informacija:
{user_text}
"""

    if lang == "no":
        return f"""
Svar KUN på norsk.

Lag dokumentet: {doc}.

Krav:
- Dokumentet skal være formelt, tydelig og klart til kopiering.
- Bruk felter brukeren kan fylle ut: [Navn], [Adresse], [E-post], [Telefon], [Dato].
- Ikke anbefal å kontakte rådgivere, advokater eller eksterne spesialister.
- Hvis opplysninger mangler, bruk hakeparenteser.
- Legg til en liste over vedlegg nederst.

Saksinformasjon:
{user_text}
"""

    return f"""
Answer ONLY in English.

Prepare this document: {doc}.

Requirements:
- The document must be formal, clear and ready to copy.
- Include placeholders for user details: [Full name], [Address], [Email], [Phone], [Date].
- Do not suggest consulting lawyers, advisors or external specialists.
- If information is missing, use square brackets.
- Add an attachments list at the end.

Case information:
{user_text}
"""


async def ask_openai(user_text, lang, full=False):
    task = build_task(user_text, lang, full)

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


async def ask_openai_document(user_text, lang, doc_type):
    task = build_document_task(user_text, lang, doc_type)

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇱🇹 Lietuvių", callback_data="lang_lt")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton("🇳🇴 Norsk", callback_data="lang_no")],
    ]

    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚖️ Jurist AI helps with consumer rights, complaints, refunds, chargebacks and legal claims.\n\nUse /start to begin."
    )


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = get_user(query.from_user.id)
    lang = query.data.replace("lang_", "")
    user["lang"] = lang

    await query.edit_message_text(LANG_TEXT[lang]["describe"])


async def send_document_menu(message, lang):
    if lang == "lt":
        keyboard = [
            [InlineKeyboardButton("📄 Pretenzija pardavėjui", callback_data="doc_seller_claim")],
            [InlineKeyboardButton("💳 Chargeback prašymas bankui", callback_data="doc_chargeback")],
            [InlineKeyboardButton("⚖️ Skundas institucijai", callback_data="doc_authority_complaint")],
        ]
    elif lang == "no":
        keyboard = [
            [InlineKeyboardButton("📄 Klagebrev til selger", callback_data="doc_seller_claim")],
            [InlineKeyboardButton("💳 Chargeback til banken", callback_data="doc_chargeback")],
            [InlineKeyboardButton("⚖️ Klage til myndighet", callback_data="doc_authority_complaint")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("📄 Seller complaint letter", callback_data="doc_seller_claim")],
            [InlineKeyboardButton("💳 Bank chargeback request", callback_data="doc_chargeback")],
            [InlineKeyboardButton("⚖️ Consumer authority complaint", callback_data="doc_authority_complaint")],
        ]

    await message.reply_text(
        LANG_TEXT[lang]["choose_doc"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_user(user_id)

    if not user["lang"]:
        await start(update, context)
        return

    user["case_text"] = update.message.text
    user["unlocked"] = False

    await update.message.reply_text(LANG_TEXT[user["lang"]]["thinking"])

    answer = await ask_openai(user["case_text"], user["lang"], full=False)
    user["last_answer"] = answer

    keyboard = [
        [InlineKeyboardButton(LANG_TEXT[user["lang"]]["unlock"], callback_data="unlock")]
    ]

    await update.message.reply_text(answer[:3900])
    await update.message.reply_text(
        LANG_TEXT[user["lang"]]["free_done"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def unlock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = get_user(query.from_user.id)
    lang = user.get("lang") or "en"

    if not user.get("case_text"):
        await query.message.reply_text(LANG_TEXT[lang]["no_case"])
        return

    if TEST_MODE:
        user["unlocked"] = True
        await query.message.reply_text(LANG_TEXT[lang]["test"])
        await query.message.reply_text(LANG_TEXT[lang]["paid"])

        answer = await ask_openai(user["case_text"], lang, full=True)
        user["last_answer"] = answer

        for i in range(0, len(answer), 3900):
            await query.message.reply_text(answer[i:i + 3900])

        await query.message.reply_text(LANG_TEXT[lang]["doc_menu"])
        await send_document_menu(query.message, lang)
        return

    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title="Jurist AI Full Case Review",
        description="Unlock full Jurist AI consumer rights case review.",
        payload=f"jurist_ai_unlock_{query.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Full case review", UNLOCK_PRICE_STARS)],
    )


async def document_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = get_user(query.from_user.id)
    lang = user.get("lang") or "en"

    if not user.get("case_text"):
        await query.message.reply_text(LANG_TEXT[lang]["no_case"])
        return

    if not user.get("unlocked"):
        await query.message.reply_text(LANG_TEXT[lang]["free_done"])
        return

    doc_type = query.data.replace("doc_", "")

    await query.message.reply_text(LANG_TEXT[lang]["doc_thinking"])

    document = await ask_openai_document(user["case_text"], lang, doc_type)

    for i in range(0, len(document), 3900):
        await query.message.reply_text(document[i:i + 3900])


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_user(user_id)
    lang = user.get("lang") or "en"
    user["unlocked"] = True

    await update.message.reply_text(LANG_TEXT[lang]["paid"])

    answer = await ask_openai(user["case_text"], lang, full=True)
    user["last_answer"] = answer

    for i in range(0, len(answer), 3900):
        await update.message.reply_text(answer[i:i + 3900])

    await update.message.reply_text(LANG_TEXT[lang]["doc_menu"])
    await send_document_menu(update.message, lang)


application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CallbackQueryHandler(language_callback, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(unlock_callback, pattern="^unlock$"))
application.add_handler(CallbackQueryHandler(document_callback, pattern="^doc_"))
application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
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
    return "Jurist AI webhook bot is running."


@web_app.post("/webhook")
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    loop.run_until_complete(application.process_update(update))
    return "OK", 200
