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

Language:
- Reply only in the user's selected language.
- Never mix languages.

Style:
- Use grammatically correct, natural and professional language.
- Avoid repetition.
- Use clear complete sentences.
- Keep answers practical and structured.

Rules:
- Do not claim to be a lawyer.
- Do not suggest consulting lawyers or external specialists.
- Provide practical next steps yourself.
- Mention institutions only as concrete action targets.
- Separate facts from assumptions.

Domains:
- Consumer rights
- Refunds
- Payment disputes
- Complaints
- Fraud cases
- Contract disputes
- Online purchases
- Warranty issues
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
        "after_full": "❓ Norite patikslinti bylą?\n\nGalite užduoti papildomą klausimą arba sugeneruoti dokumentą pagal šią bylą.",
        "ask_prompt": "❓ Parašykite papildomą klausimą dėl šios bylos.",
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
        "after_full": "❓ Would you like to clarify the case?\n\nYou can ask a follow-up question or generate a document based on this case.",
        "ask_prompt": "❓ Write your follow-up question about this case.",
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
        "after_full": "❓ Vil du avklare saken videre?\n\nDu kan stille et oppfølgingsspørsmål eller generere et dokument basert på saken.",
        "ask_prompt": "❓ Skriv oppfølgingsspørsmålet ditt om denne saken.",
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
            "awaiting_followup": False,
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
            "chargeback": "prašymą bankui dėl pinigų grąžinimo",
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
Atsakyk tik lietuviškai.

Paruošk pilną bylos vertinimą:

📋 Situacijos santrauka
📌 Pagrindiniai faktai
⚖️ Galimi pažeidimai
📂 Reikalingi įrodymai
📊 Bylos stiprumas 0-100
🎯 Veiksmų planas
📄 Galimi dokumentai
💳 Galimybė susigrąžinti pinigus per banką
✅ Išvada

🧭 3-5 klausimai bylai patikslinti
💡 3 praktiniai pasiūlymai

Nesiūlyk kreiptis į konsultantus ar teisininkus. Pateik konkrečius veiksmus pats.

Situacija:
{user_text}
"""
        return f"""
Atsakyk tik lietuviškai.

Paruošk trumpą pirminį vertinimą:

📋 Kategorija
⚠️ Pagrindinė problema
📂 Trūkstami įrodymai
🎯 Pirmas rekomenduojamas veiksmas
🔓 Išplėstinio atsakymo galimybė

Nesiūlyk kreiptis į konsultantus ar teisininkus.

Situacija:
{user_text}
"""

    if lang == "no":
        if full:
            return f"""
Svar kun på norsk.

Lag en full vurdering:

📋 Situasjonsoppsummering
📌 Viktige fakta
⚖️ Mulige brudd
📂 Nødvendige bevis
📊 Sakens styrke 0-100
🎯 Handlingsplan
📄 Mulige dokumenter
💳 Mulighet for tilbakebetaling via bank
✅ Konklusjon

🧭 3-5 oppfølgingsspørsmål
💡 3 praktiske forslag

Ikke anbefal eksterne rådgivere eller advokater. Gi konkrete tiltak selv.

Situasjon:
{user_text}
"""
        return f"""
Svar kun på norsk.

Lag en kort førstevurdering:

📋 Kategori
⚠️ Hovedproblem
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

Prepare a full case review:

📋 Situation summary
📌 Key facts
⚖️ Possible violations
📂 Evidence needed
📊 Case strength 0-100
🎯 Action plan
📄 Possible documents
💳 Bank refund or chargeback option
✅ Conclusion

🧭 3-5 follow-up questions
💡 3 practical suggestions

Do not suggest external lawyers or advisors. Provide concrete next steps yourself.

User situation:
{user_text}
"""
    return f"""
Answer only in English.

Prepare a short initial assessment:

📋 Likely category
⚠️ Main issue
📂 Missing evidence
🎯 First recommended step
🔓 Extended response option

Do not suggest external lawyers or advisors.

User situation:
{user_text}
"""


def build_document_task(user_text, lang, doc_type):
    doc = document_name(doc_type, lang)

    if lang == "lt":
        return f"""
Atsakyk tik lietuviškai.

Paruošk dokumentą: {doc}.

Reikalavimai:
- Oficialus, gramatiškai taisyklingas tekstas.
- Paruošta siųsti.
- Naudok laukus: [Vardas Pavardė], [Adresas], [El. paštas], [Telefonas], [Data].
- Jei trūksta duomenų, naudok laužtinius skliaustus.
- Gale pridėk priedų sąrašą.
- Nesiūlyk kreiptis į konsultantus ar teisininkus.

Bylos informacija:
{user_text}
"""

    if lang == "no":
        return f"""
Svar kun på norsk.

Lag dokumentet: {doc}.

Krav:
- Formelt og grammatisk korrekt.
- Klart til sending.
- Bruk felter: [Navn], [Adresse], [E-post], [Telefon], [Dato].
- Hvis informasjon mangler, bruk hakeparenteser.
- Legg til vedleggsliste nederst.
- Ikke anbefal eksterne rådgivere eller advokater.

Saksinformasjon:
{user_text}
"""

    return f"""
Answer only in English.

Prepare this document: {doc}.

Requirements:
- Formal and grammatically correct.
- Ready to send.
- Use placeholders: [Full name], [Address], [Email], [Phone], [Date].
- If information is missing, use square brackets.
- Add an attachments list at the end.
- Do not suggest external lawyers or advisors.

Case information:
{user_text}
"""


def build_followup_task(case_text, question, lang):
    if lang == "lt":
        return f"""
Atsakyk tik lietuviškai.

Atsakyk į vartotojo papildomą klausimą pagal jau aprašytą bylą.
Būk konkretus, aiškus ir praktiškas. Nesiūlyk kreiptis į konsultantus ar teisininkus.

Bylos informacija:
{case_text}

Vartotojo klausimas:
{question}
"""

    if lang == "no":
        return f"""
Svar kun på norsk.

Svar på brukerens oppfølgingsspørsmål basert på saken.
Vær konkret, tydelig og praktisk. Ikke anbefal eksterne rådgivere eller advokater.

Saksinformasjon:
{case_text}

Brukerens spørsmål:
{question}
"""

    return f"""
Answer only in English.

Answer the user's follow-up question based on the case.
Be clear, concrete and practical. Do not suggest external lawyers or advisors.

Case information:
{case_text}

User question:
{question}
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


async def ask_openai_followup(case_text, question, lang):
    task = build_followup_task(case_text, question, lang)

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


async def send_after_full_menu(message, lang):
    if lang == "lt":
        keyboard = [
            [InlineKeyboardButton("❓ Užduoti klausimą", callback_data="ask_question")],
            [InlineKeyboardButton("📄 Generuoti dokumentą", callback_data="show_docs")],
        ]
    elif lang == "no":
        keyboard = [
            [InlineKeyboardButton("❓ Still spørsmål", callback_data="ask_question")],
            [InlineKeyboardButton("📄 Generer dokument", callback_data="show_docs")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("❓ Ask a question", callback_data="ask_question")],
            [InlineKeyboardButton("📄 Generate document", callback_data="show_docs")],
        ]

    await message.reply_text(
        LANG_TEXT[lang]["after_full"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def send_document_menu(message, lang):
    if lang == "lt":
        keyboard = [
            [InlineKeyboardButton("📄 Pretenzija pardavėjui", callback_data="doc_seller_claim")],
            [InlineKeyboardButton("💳 Prašymas bankui dėl pinigų grąžinimo", callback_data="doc_chargeback")],
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

    lang = user["lang"]

    if user.get("awaiting_followup") and user.get("case_text"):
        user["awaiting_followup"] = False
        await update.message.reply_text(LANG_TEXT[lang]["thinking"])

        answer = await ask_openai_followup(user["case_text"], update.message.text, lang)

        for i in range(0, len(answer), 3900):
            await update.message.reply_text(answer[i:i + 3900])

        await send_after_full_menu(update.message, lang)
        return

    user["case_text"] = update.message.text
    user["unlocked"] = False
    user["awaiting_followup"] = False

    await update.message.reply_text(LANG_TEXT[lang]["thinking"])

    answer = await ask_openai(user["case_text"], lang, full=False)
    user["last_answer"] = answer

    keyboard = [
        [InlineKeyboardButton(LANG_TEXT[lang]["unlock"], callback_data="unlock")]
    ]

    await update.message.reply_text(answer[:3900])
    await update.message.reply_text(
        LANG_TEXT[lang]["free_done"],
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

        await send_after_full_menu(query.message, lang)
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


async def ask_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = get_user(query.from_user.id)
    lang = user.get("lang") or "en"

    if not user.get("case_text"):
        await query.message.reply_text(LANG_TEXT[lang]["no_case"])
        return

    user["awaiting_followup"] = True
    await query.message.reply_text(LANG_TEXT[lang]["ask_prompt"])


async def show_docs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await query.message.reply_text(LANG_TEXT[lang]["doc_menu"])
    await send_document_menu(query.message, lang)


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

    await send_after_full_menu(update.message, lang)


application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CallbackQueryHandler(language_callback, pattern="^lang_"))
application.add_handler(CallbackQueryHandler(unlock_callback, pattern="^unlock$"))
application.add_handler(CallbackQueryHandler(ask_question_callback, pattern="^ask_question$"))
application.add_handler(CallbackQueryHandler(show_docs_callback, pattern="^show_docs$"))
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
