import os
import threading
from flask import Flask
from openai import OpenAI

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
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
UNLOCK_PRICE_STARS = int(os.getenv("UNLOCK_PRICE_STARS", "100"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY)

users = {}

web_app = Flask(__name__)

@web_app.get("/")
def home():
    return "Jurist AI bot is running."

def run_web():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port)

SYSTEM_PROMPT = """
You are Jurist AI – Consumer Rights & Legal Claims Specialist.

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

Always:
1. Analyze the situation.
2. Identify possible consumer rights issues.
3. List missing evidence.
4. Suggest practical next steps.
5. If needed, draft a complaint, claim, refund request, chargeback request or formal letter.
6. Be clear, structured and professional.
7. Do not claim to be a licensed lawyer.
8. Separate facts from assumptions.
9. Answer in the user's selected language.
"""

LANG_TEXT = {
    "lt": {
        "welcome": "👋 Sveiki, aš esu Jurist AI.\n\nPadedu vartotojų teisių, pretenzijų, pinigų grąžinimo, chargeback ir skundų klausimais.\n\nPasirinkite kalbą:",
        "describe": "✍️ Trumpai aprašykite savo situaciją.\n\nPvz.: pirkau prekę, jos negavau, pardavėjas neatsako, mokėjau kortele.",
        "free_done": "🔎 Tai trumpas nemokamas vertinimas.\n\nNorint gauti pilną analizę, veiksmų planą ir dokumento juodraštį, atrakinkite pilną analizę.",
        "unlock": f"🔓 Atrakinti pilną analizę – ⭐{UNLOCK_PRICE_STARS}",
        "paid": "✅ Apmokėjimas gautas. Ruošiu pilną Jurist AI analizę...",
    },
    "en": {
        "welcome": "👋 Welcome to Jurist AI.\n\nI help with consumer rights, complaints, refunds, chargebacks and legal claims.\n\nChoose your language:",
        "describe": "✍️ Briefly describe your situation.\n\nExample: I bought an item online, did not receive it, seller does not respond, I paid by card.",
        "free_done": "🔎 This is a short free assessment.\n\nUnlock the full analysis to receive a full legal-style review, action plan and document draft.",
        "unlock": f"🔓 Unlock full analysis – ⭐{UNLOCK_PRICE_STARS}",
        "paid": "✅ Payment received. Preparing full Jurist AI analysis...",
    },
    "no": {
        "welcome": "👋 Velkommen til Jurist AI.\n\nJeg hjelper med forbrukerrettigheter, klager, refusjon, chargeback og juridiske krav.\n\nVelg språk:",
        "describe": "✍️ Beskriv kort situasjonen din.\n\nEksempel: Jeg kjøpte en vare på nett, fikk den ikke, selger svarer ikke, jeg betalte med kort.",
        "free_done": "🔎 Dette er en kort gratis vurdering.\n\nLås opp full analyse for å få komplett vurdering, handlingsplan og dokumentutkast.",
        "unlock": f"🔓 Lås opp full analyse – ⭐{UNLOCK_PRICE_STARS}",
        "paid": "✅ Betaling mottatt. Forbereder full Jurist AI-analyse...",
    },
}

def get_user(user_id):
    if user_id not in users:
        users[user_id] = {
            "lang": None,
            "case_text": None,
            "unlocked": False,
        }
    return users[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇱🇹 Lietuvių", callback_data="lang_lt")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton("🇳🇴 Norsk", callback_data="lang_no")],
    ]

    await update.message.reply_text(
        LANG_TEXT["en"]["welcome"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = get_user(user_id)

    lang = query.data.replace("lang_", "")
    user["lang"] = lang

    await query.edit_message_text(LANG_TEXT[lang]["describe"])

async def ask_openai(user_text, lang, full=False):
    if full:
        task = f"""
Prepare a FULL Jurist AI analysis.

Include:
1. Situation summary
2. Key facts
3. Possible consumer rights violations
4. Evidence needed
5. Case strength score from 0 to 100
6. Recommended action plan
7. Draft formal complaint / claim letter
8. Chargeback option if relevant
9. Final conclusion

User situation:
{user_text}
"""
    else:
        task = f"""
Prepare a SHORT FREE initial assessment.

Include only:
1. Likely category
2. Main issue
3. Missing evidence
4. Whether the case may be worth pursuing
5. Tell the user that full analysis can be unlocked.

User situation:
{user_text}
"""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + f"\nSelected language: {lang}"},
            {"role": "user", "content": task},
        ],
    )

    return response.choices[0].message.content

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_user(user_id)

    if not user["lang"]:
        await start(update, context)
        return

    user_text = update.message.text
    user["case_text"] = user_text
    user["unlocked"] = False

    await update.message.reply_text("⏳ Analizuoju...")

    answer = await ask_openai(user_text, user["lang"], full=False)

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

    user_id = query.from_user.id
    user = get_user(user_id)

    if not user.get("case_text"):
        await query.message.reply_text(LANG_TEXT[user["lang"]]["describe"])
        return

    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title="Jurist AI Full Analysis",
        description="Unlock full Jurist AI consumer rights analysis.",
        payload=f"jurist_ai_unlock_{user_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Full analysis", UNLOCK_PRICE_STARS)],
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_user(user_id)

    user["unlocked"] = True

    await update.message.reply_text(LANG_TEXT[user["lang"]]["paid"])

    answer = await ask_openai(user["case_text"], user["lang"], full=True)

    for i in range(0, len(answer), 3900):
        await update.message.reply_text(answer[i:i+3900])

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Jurist AI helps with consumer rights, complaints, refunds, chargebacks and legal claims.\n\nUse /start to begin."
    )

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN")
    if not OPENAI_API_KEY:
        raise ValueError("Missing OPENAI_API_KEY")

    threading.Thread(target=run_web, daemon=True).start()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(language_callback, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(unlock_callback, pattern="^unlock$"))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
