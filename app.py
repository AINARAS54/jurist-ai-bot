import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

JURIST_PROMPT = """
You are Jurist AI.

You specialize in:
- Consumer rights
- Refunds
- Chargebacks
- Complaints
- Fraud cases
- Contract disputes

Always:
1. Analyze the situation.
2. Identify possible violations.
3. List missing evidence.
4. Suggest next steps.
5. Write clearly and professionally.

Answer in the language used by the user.
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Jurist AI\n\n"
        "Describe your situation and I will analyze it."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[
            {"role": "system", "content": JURIST_PROMPT},
            {"role": "user", "content": user_text}
        ]
    )

    answer = response.choices[0].message.content

    await update.message.reply_text(answer[:4000])

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
