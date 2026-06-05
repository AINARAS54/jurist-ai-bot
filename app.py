import os
import json
import logging
import re
from datetime import datetime
from html import escape

from flask import Flask, request, jsonify
import requests

# ==========================================================
# Justice AI - Telegram Webhook + Document Generator
# Final app.py
#
# ENV variables required:
# TELEGRAM_BOT_TOKEN=123456:ABC...
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4.1-mini   (optional)
# PUBLIC_BASE_URL=https://your-domain.com   (optional)
#
# Webhook URL:
# https://your-domain.com/telegram-webhook
#
# Health check:
# https://your-domain.com/
# ==========================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("justice_ai")

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""


# -----------------------------
# Basic case memory
# For production, replace with Supabase/Postgres.
# -----------------------------
CASES = {}


def now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def make_case_id(chat_id: int) -> str:
    existing = CASES.get(chat_id)
    if existing and existing.get("case_id"):
        return existing["case_id"]
    return f"CASE-{datetime.now().year}-{str(len(CASES) + 1).zfill(6)}"


def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    if not TELEGRAM_API:
        logger.error("TELEGRAM_BOT_TOKEN is missing.")
        return None

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    r = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)
    if not r.ok:
        logger.error("Telegram sendMessage error: %s %s", r.status_code, r.text)
    return r.json() if r.ok else None


def answer_callback(callback_query_id: str, text: str = ""):
    if not TELEGRAM_API:
        return
    requests.post(
        f"{TELEGRAM_API}/answerCallbackQuery",
        json={"callback_query_id": callback_query_id, "text": text},
        timeout=10,
    )


def main_menu():
    return {
        "inline_keyboard": [
            [{"text": "📄 Generuoti dokumentą", "callback_data": "generate_document"}],
            [{"text": "🏛 Parinkti instituciją", "callback_data": "select_institution"}],
            [{"text": "⚖️ Teisinis vertinimas", "callback_data": "legal_review"}],
        ]
    }


# -----------------------------
# Situation classifier
# -----------------------------
CASE_RULES = [
    {
        "category": "Nuotolinė prekyba / negauta prekė",
        "keywords": ["negavau", "nepristatė", "neatsiuntė", "siunta", "kurjer", "užsakymas", "internetu"],
        "document_title": "PRETENZIJA DĖL PREKĖS NEPRISTATYMO",
        "recipient": "Pardavėjas / paslaugos teikėjas",
        "copy_to": "Valstybinė vartotojų teisių apsaugos tarnyba (VVTAT), jei pardavėjas nepateiks atsakymo arba atsisakys spręsti ginčą.",
        "laws": [
            "Lietuvos Respublikos civilinio kodekso 6.359 straipsnis – vartojimo pirkimo-pardavimo sutarties vykdymas.",
            "Lietuvos Respublikos vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą."
        ],
        "request": "Prašau pristatyti prekę arba grąžinti sumokėtus pinigus."
    },
    {
        "category": "Brokuota / nekokybiška prekė",
        "keywords": ["brokuota", "gedimas", "sugedo", "neveikia", "defektas", "garantija", "nekokybiška", "neveikė"],
        "document_title": "PRETENZIJA DĖL NEKOKYBIŠKOS PREKĖS",
        "recipient": "Pardavėjas / paslaugos teikėjas",
        "copy_to": "Valstybinė vartotojų teisių apsaugos tarnyba (VVTAT), jei pardavėjas nepateiks atsakymo arba atsisakys spręsti ginčą.",
        "laws": [
            "Lietuvos Respublikos civilinio kodekso 6.363 straipsnis – vartotojo teisės, kai parduota netinkamos kokybės prekė.",
            "Lietuvos Respublikos vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą."
        ],
        "request": "Prašau pašalinti prekės trūkumus, pakeisti prekę tinkama arba grąžinti sumokėtus pinigus."
    },
    {
        "category": "Pinigų negrąžinimas / sutarties atsisakymas",
        "keywords": ["negrąžina", "negrąžino", "pinigų grąžinimas", "refund", "atsisakiau", "atšaukiau", "grąžinti pinigus"],
        "document_title": "PRETENZIJA DĖL PINIGŲ GRĄŽINIMO",
        "recipient": "Pardavėjas / paslaugos teikėjas",
        "copy_to": "Valstybinė vartotojų teisių apsaugos tarnyba (VVTAT), jei pardavėjas nepateiks atsakymo arba atsisakys spręsti ginčą.",
        "laws": [
            "Lietuvos Respublikos civilinio kodekso 6.22810 straipsnis – vartotojo teisė atsisakyti nuotolinės sutarties.",
            "Lietuvos Respublikos vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą."
        ],
        "request": "Prašau grąžinti sumokėtus pinigus į tą pačią mokėjimo priemonę, kuria buvo atliktas mokėjimas."
    },
    {
        "category": "Bankas / mokėjimo paslauga",
        "keywords": ["bankas", "kortelė", "mokėjimas", "pavedimas", "chargeback", "visa", "mastercard", "sąskaita"],
        "document_title": "SKUNDAS DĖL MOKĖJIMO PASLAUGOS",
        "recipient": "Bankas / mokėjimo paslaugų teikėjas",
        "copy_to": "Lietuvos bankas, jei finansų įstaiga nepateiks atsakymo arba atsisakys spręsti ginčą.",
        "laws": [
            "Lietuvos Respublikos mokėjimų įstatymas – mokėjimo paslaugų naudotojo teisės ir pareigos.",
            "Lietuvos banko nustatyta finansinių paslaugų ginčų nagrinėjimo tvarka."
        ],
        "request": "Prašau išnagrinėti mokėjimo operaciją ir pateikti sprendimą dėl lėšų grąžinimo galimybės."
    },
    {
        "category": "Draudimo ginčas",
        "keywords": ["draudimas", "draudimo", "žala", "išmoka", "kompensacija", "polisas"],
        "document_title": "SKUNDAS DĖL DRAUDIMO IŠMOKOS",
        "recipient": "Draudimo bendrovė",
        "copy_to": "Lietuvos bankas, jei draudimo bendrovė nepateiks atsakymo arba atsisakys spręsti ginčą.",
        "laws": [
            "Lietuvos Respublikos draudimo įstatymas – draudimo sutarties vykdymas.",
            "Lietuvos banko nustatyta finansinių paslaugų ginčų nagrinėjimo tvarka."
        ],
        "request": "Prašau išnagrinėti žalą ir pateikti motyvuotą sprendimą dėl draudimo išmokos."
    },
    {
        "category": "Telekomunikacijos / internetas",
        "keywords": ["internetas", "ryšys", "operatorius", "telefonas", "mobilus", "tele2", "telia", "bitė", "sąskaita už ryšį"],
        "document_title": "PRETENZIJA DĖL RYŠIO PASLAUGŲ",
        "recipient": "Ryšio paslaugų teikėjas",
        "copy_to": "Ryšių reguliavimo tarnyba (RRT), jei paslaugų teikėjas nepateiks atsakymo arba atsisakys spręsti ginčą.",
        "laws": [
            "Lietuvos Respublikos elektroninių ryšių įstatymas – elektroninių ryšių paslaugų naudotojo teisės.",
            "Vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą."
        ],
        "request": "Prašau išnagrinėti paslaugos teikimo problemą ir pateikti sprendimą."
    },
    {
        "category": "Energijos tiekimas",
        "keywords": ["elektra", "dujos", "energija", "tiekėjas", "sąskaita už elektrą", "eso", "ignitis"],
        "document_title": "PRETENZIJA DĖL ENERGIJOS PASLAUGŲ",
        "recipient": "Energijos paslaugų teikėjas",
        "copy_to": "Valstybinė energetikos reguliavimo taryba (VERT), jei tiekėjas nepateiks atsakymo arba atsisakys spręsti ginčą.",
        "laws": [
            "Lietuvos Respublikos energetikos įstatymas – vartotojų teisės energetikos sektoriuje.",
            "Vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą."
        ],
        "request": "Prašau išnagrinėti ginčą dėl energijos paslaugų ir pateikti sprendimą."
    },
    {
        "category": "Asmens duomenys",
        "keywords": ["asmens duomenys", "duomenys", "privatumas", "gdpr", "nutekino", "neteisėtai tvarko"],
        "document_title": "SKUNDAS DĖL ASMENS DUOMENŲ TVARKYMO",
        "recipient": "Duomenis tvarkanti įmonė / organizacija",
        "copy_to": "Valstybinė duomenų apsaugos inspekcija (VDAI), jei duomenų valdytojas nepateiks atsakymo arba atsisakys spręsti problemą.",
        "laws": [
            "Bendrasis duomenų apsaugos reglamentas (BDAR / GDPR).",
            "Lietuvos Respublikos asmens duomenų teisinės apsaugos įstatymas."
        ],
        "request": "Prašau paaiškinti duomenų tvarkymo pagrindą ir pašalinti galimą pažeidimą."
    },
    {
        "category": "Galimo sukčiavimo požymiai",
        "keywords": ["sukčiavimas", "apgavo", "scam", "fraud", "netikra", "melavo", "praradau pinigus"],
        "document_title": "PAREIŠKIMAS DĖL GALIMO SUKČIAVIMO",
        "recipient": "Policija",
        "copy_to": "VVTAT arba Lietuvos bankas – pagal situacijos pobūdį.",
        "laws": [
            "Lietuvos Respublikos baudžiamojo kodekso 182 straipsnis – sukčiavimas, kai yra tyčinio apgaulės būdu padarytos žalos požymių.",
            "Vartotojų teisių apsaugos įstatymas – jei ginčas susijęs su vartojimo santykiais."
        ],
        "request": "Prašau įvertinti pateiktas aplinkybes ir pradėti tyrimą, jei bus nustatyti galimos nusikalstamos veikos požymiai."
    },
]


def normalize_text(text: str) -> str:
    return (text or "").lower()


def classify_case(text: str) -> dict:
    t = normalize_text(text)
    best_rule = None
    best_score = 0

    for rule in CASE_RULES:
        score = sum(1 for kw in rule["keywords"] if kw in t)
        if score > best_score:
            best_score = score
            best_rule = rule

    if best_rule:
        return best_rule

    return {
        "category": "Bendras vartotojo ginčas",
        "document_title": "PRETENZIJA DĖL GALIMO VARTOTOJO TEISIŲ PAŽEIDIMO",
        "recipient": "Pardavėjas / paslaugos teikėjas",
        "copy_to": "Valstybinė vartotojų teisių apsaugos tarnyba (VVTAT), jei ginčo nepavyks išspręsti tiesiogiai.",
        "laws": [
            "Lietuvos Respublikos vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą.",
            "Lietuvos Respublikos civilinis kodeksas – vartojimo sutarčių vykdymas."
        ],
        "request": "Prašau išnagrinėti situaciją ir pateikti sprendimą."
    }


def extract_company_name(text: str) -> str:
    # Simple extraction. In production, use structured questions.
    patterns = [
        r"(?:įmonė|pardavėjas|parduotuvė|paslaugų teikėjas)\s*[:\-]\s*([A-Za-zĄČĘĖĮŠŲŪŽąčęėįšųūž0-9\"„“\s\.\-]+)",
        r"(?:iš|pas)\s+(UAB\s+[A-Za-zĄČĘĖĮŠŲŪŽąčęėįšųūž0-9\"„“\s\.\-]+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            value = m.group(1).strip()
            return value[:80]
    return ""


def clean_summary(user_text: str) -> str:
    text = " ".join((user_text or "").split())
    if len(text) > 520:
        text = text[:520].rsplit(" ", 1)[0] + "..."
    return text


def build_document(chat_id: int) -> str:
    case = CASES.get(chat_id, {})
    user_text = case.get("user_text", "")
    case_id = case.get("case_id", make_case_id(chat_id))
    rule = classify_case(user_text)
    company = extract_company_name(user_text)

    recipient = company if company else rule["recipient"]
    summary = clean_summary(user_text)

    laws_text = "\n".join([f"- {law}" for law in rule["laws"]])

    # Keep concise for Telegram.
    document = f"""
KAM:

{recipient}
[Adresas]
[El. paštas]

KOPIJA:

{rule["copy_to"]}

NUO:

[Vardas Pavardė]
[Adresas]
[El. paštas]
[Telefonas]

BYLOS NUMERIS: {case_id}

{rule["document_title"]}

Sveiki,

Situacijos santrauka:

{summary if summary else "[Trumpai aprašykite situaciją.]"}

⚖️ Pirminis teisinis vertinimas:

- Remiantis pateikta informacija, gali būti pažeistos vartotojo teisės.
- Reikalingas pardavėjo ar paslaugų teikėjo motyvuotas atsakymas.

⚖️ Taikytini teisės aktai:

{laws_text}

Reikalavimas:

{rule["request"]}

Prašau pateikti atsakymą teisės aktų nustatyta tvarka.

Pagarbiai,

[Vardas Pavardė]

Data: {now_date()}
""".strip()

    return document


def build_legal_preview(chat_id: int) -> str:
    case = CASES.get(chat_id, {})
    user_text = case.get("user_text", "")
    rule = classify_case(user_text)

    laws = "\n".join([f"• {escape(law)}" for law in rule["laws"]])

    return (
        f"🔎 <b>Nustatyta kategorija:</b>\n"
        f"{escape(rule['category'])}\n\n"
        f"📑 <b>Dokumentas:</b>\n"
        f"{escape(rule['document_title'])}\n\n"
        f"🏛 <b>Adresatas:</b>\n"
        f"{escape(rule['recipient'])}\n\n"
        f"⚖️ <b>Taikomi teisės aktai:</b>\n"
        f"{laws}"
    )


def openai_short_assessment(user_text: str) -> str:
    if not OPENAI_API_KEY:
        return ""

    system_prompt = (
        "Tu esi Justice AI vartotojų teisių asistentas Lietuvoje. "
        "Atsakyk trumpai, aiškiai, lietuviškai. "
        "Neteik kategoriškų kaltinimų. "
        "Naudok formuluotę 'gali būti pažeistos vartotojo teisės'. "
        "Klausimų turi būti ne daugiau kaip 3, pasiūlymų ne daugiau kaip 2."
    )

    user_prompt = f"""
Įvertink vartotojo situaciją ir pateik:
1) trumpą situacijos santrauką,
2) pirminį teisinį vertinimą iki 2 punktų,
3) iki 3 klausimų bylai patikslinti,
4) iki 2 praktinių pasiūlymų.

Situacija:
{user_text}
""".strip()

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            },
            timeout=45,
        )
        if not r.ok:
            logger.error("OpenAI error: %s %s", r.status_code, r.text)
            return ""
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.exception("OpenAI request failed: %s", e)
        return ""


# -----------------------------
# Routes
# -----------------------------
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "Justice AI",
        "telegram_token": bool(TELEGRAM_BOT_TOKEN),
        "openai_key": bool(OPENAI_API_KEY),
    })


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(force=True, silent=True) or {}

    try:
        if "callback_query" in update:
            cq = update["callback_query"]
            callback_id = cq.get("id")
            data = cq.get("data", "")
            message = cq.get("message", {})
            chat_id = message.get("chat", {}).get("id")

            answer_callback(callback_id)

            if not chat_id:
                return jsonify({"ok": True})

            if data == "generate_document":
                send_message(chat_id, "⏳ Ruošiu dokumentą...")
                document = build_document(chat_id)
                send_message(chat_id, f"<pre>{escape(document)}</pre>")
                return jsonify({"ok": True})

            if data == "select_institution":
                preview = build_legal_preview(chat_id)
                send_message(chat_id, preview, reply_markup=main_menu())
                return jsonify({"ok": True})

            if data == "legal_review":
                case = CASES.get(chat_id, {})
                user_text = case.get("user_text", "")
                if not user_text:
                    send_message(chat_id, "Pirmiausia trumpai aprašykite situaciją.")
                    return jsonify({"ok": True})

                assessment = openai_short_assessment(user_text)
                if not assessment:
                    assessment = (
                        "⚖️ Pirminis teisinis vertinimas:\n"
                        "- Remiantis pateikta informacija, gali būti pažeistos vartotojo teisės.\n"
                        "- Reikalingas papildomas aplinkybių įvertinimas.\n\n"
                        "Klausimai bylai patikslinti:\n"
                        "1. Kada įvyko situacija?\n"
                        "2. Ar turite pirkimo ar mokėjimo įrodymą?\n"
                        "3. Ar gavote raštišką atsakymą?\n\n"
                        "Praktiniai pasiūlymai:\n"
                        "1. Išsaugokite visus įrodymus.\n"
                        "2. Pateikite rašytinę pretenziją."
                    )
                send_message(chat_id, escape(assessment).replace("\n", "\n"), reply_markup=main_menu())
                return jsonify({"ok": True})

            return jsonify({"ok": True})

        if "message" in update:
            msg = update["message"]
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")

            if not chat_id:
                return jsonify({"ok": True})

            if text.startswith("/start"):
                CASES[chat_id] = {
                    "case_id": make_case_id(chat_id),
                    "user_text": "",
                    "created_at": now_date(),
                }
                send_message(
                    chat_id,
                    "⚖️ <b>Justice AI</b>\n\nTrumpai aprašykite vartotojo teisių situaciją. "
                    "Po aprašymo galėsite sugeneruoti dokumentą su tinkamais teisės aktais ir institucija.",
                )
                return jsonify({"ok": True})

            # Save situation
            case_id = make_case_id(chat_id)
            CASES[chat_id] = {
                "case_id": case_id,
                "user_text": text,
                "created_at": now_date(),
            }

            rule = classify_case(text)
            company = extract_company_name(text)
            recipient = company if company else rule["recipient"]

            response = (
                f"📁 <b>Bylos numeris:</b> {escape(case_id)}\n\n"
                f"🔎 <b>Nustatyta kategorija:</b>\n{escape(rule['category'])}\n\n"
                f"🏛 <b>Adresatas:</b>\n{escape(recipient)}\n\n"
                f"📑 <b>Dokumento tipas:</b>\n{escape(rule['document_title'])}\n\n"
                f"Pasirinkite veiksmą:"
            )

            send_message(chat_id, response, reply_markup=main_menu())
            return jsonify({"ok": True})

    except Exception as e:
        logger.exception("Webhook error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})


@app.route("/set-webhook", methods=["GET"])
def set_webhook():
    if not TELEGRAM_API:
        return jsonify({"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"}), 400

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not public_base_url:
        return jsonify({"ok": False, "error": "PUBLIC_BASE_URL missing"}), 400

    webhook_url = public_base_url.rstrip("/") + "/telegram-webhook"

    r = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": webhook_url}, timeout=20)
    return jsonify(r.json())


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
