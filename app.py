import os
import logging
import re
from datetime import datetime
from html import escape

from flask import Flask, request, jsonify
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("justice_ai")

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""

CASES = {}
USER_STATES = {}

INSTITUTIONS = {
    "VVTAT": {
        "name": "Valstybinė vartotojų teisių apsaugos tarnyba",
        "address": "Vilniaus g. 25, LT-01402 Vilnius",
        "email": "tarnyba@vvtat.lt",
    },
    "LB": {
        "name": "Lietuvos bankas",
        "address": "Gedimino pr. 6, LT-01103 Vilnius",
        "email": "info@lb.lt",
    },
    "RRT": {
        "name": "Ryšių reguliavimo tarnyba",
        "address": "Mortos g. 14, LT-03219 Vilnius",
        "email": "rrt@rrt.lt",
    },
    "VERT": {
        "name": "Valstybinė energetikos reguliavimo taryba",
        "address": "Verkių g. 25C-1, LT-08223 Vilnius",
        "email": "info@vert.lt",
    },
    "VDAI": {
        "name": "Valstybinė duomenų apsaugos inspekcija",
        "address": "L. Sapiegos g. 17, LT-10312 Vilnius",
        "email": "ada@ada.lt",
    },
    "POLICIJA": {
        "name": "Policija",
        "address": "Per ePolicija sistemą arba artimiausią policijos komisariatą",
        "email": "www.epolicija.lt",
    },
}

CASE_RULES = [
    {
        "category": "Nuotolinė prekyba / negauta prekė",
        "keywords": ["negavau", "nepristatė", "neatsiuntė", "siunta", "kurjer", "užsakymas", "internetu"],
        "document_title": "PRETENZIJA DĖL PREKĖS NEPRISTATYMO",
        "recipient": "Pardavėjas / paslaugos teikėjas",
        "copy_key": "VVTAT",
        "laws": [
            "Lietuvos Respublikos civilinio kodekso 6.359 straipsnis – vartojimo pirkimo-pardavimo sutarties vykdymas.",
            "Lietuvos Respublikos vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą.",
        ],
        "request": "Prašau pristatyti prekę arba grąžinti sumokėtus pinigus.",
    },
    {
        "category": "Brokuota / nekokybiška prekė",
        "keywords": ["brokuota", "gedimas", "sugedo", "neveikia", "defektas", "garantija", "nekokybiška", "neveikė"],
        "document_title": "PRETENZIJA DĖL NEKOKYBIŠKOS PREKĖS",
        "recipient": "Pardavėjas / paslaugos teikėjas",
        "copy_key": "VVTAT",
        "laws": [
            "Lietuvos Respublikos civilinio kodekso 6.363 straipsnis – vartotojo teisės, kai parduota netinkamos kokybės prekė.",
            "Lietuvos Respublikos vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą.",
        ],
        "request": "Prašau pašalinti prekės trūkumus, pakeisti prekę tinkama arba grąžinti sumokėtus pinigus.",
    },
    {
        "category": "Pinigų negrąžinimas / sutarties atsisakymas",
        "keywords": ["negrąžina", "negrąžino", "pinigų grąžinimas", "refund", "atsisakiau", "atšaukiau", "grąžinti pinigus"],
        "document_title": "PRETENZIJA DĖL PINIGŲ GRĄŽINIMO",
        "recipient": "Pardavėjas / paslaugos teikėjas",
        "copy_key": "VVTAT",
        "laws": [
            "Lietuvos Respublikos civilinio kodekso 6.22810 straipsnis – vartotojo teisė atsisakyti nuotolinės sutarties.",
            "Lietuvos Respublikos vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą.",
        ],
        "request": "Prašau grąžinti sumokėtus pinigus.",
    },
    {
        "category": "Bankas / mokėjimo paslauga",
        "keywords": ["bankas", "kortelė", "mokėjimas", "pavedimas", "chargeback", "visa", "mastercard", "sąskaita"],
        "document_title": "SKUNDAS DĖL MOKĖJIMO PASLAUGOS",
        "recipient": "Bankas / mokėjimo paslaugų teikėjas",
        "copy_key": "LB",
        "laws": [
            "Lietuvos Respublikos mokėjimų įstatymas – mokėjimo paslaugų naudotojo teisės ir pareigos.",
            "Lietuvos banko nustatyta finansinių paslaugų ginčų nagrinėjimo tvarka.",
        ],
        "request": "Prašau išnagrinėti mokėjimo operaciją ir pateikti sprendimą dėl lėšų grąžinimo galimybės.",
    },
    {
        "category": "Draudimo ginčas",
        "keywords": ["draudimas", "draudimo", "žala", "išmoka", "kompensacija", "polisas"],
        "document_title": "SKUNDAS DĖL DRAUDIMO IŠMOKOS",
        "recipient": "Draudimo bendrovė",
        "copy_key": "LB",
        "laws": [
            "Lietuvos Respublikos draudimo įstatymas – draudimo sutarties vykdymas.",
            "Lietuvos banko nustatyta finansinių paslaugų ginčų nagrinėjimo tvarka.",
        ],
        "request": "Prašau išnagrinėti žalą ir pateikti motyvuotą sprendimą dėl draudimo išmokos.",
    },
    {
        "category": "Telekomunikacijos / internetas",
        "keywords": ["internetas", "ryšys", "operatorius", "telefonas", "mobilus", "tele2", "telia", "bitė"],
        "document_title": "PRETENZIJA DĖL RYŠIO PASLAUGŲ",
        "recipient": "Ryšio paslaugų teikėjas",
        "copy_key": "RRT",
        "laws": [
            "Lietuvos Respublikos elektroninių ryšių įstatymas – elektroninių ryšių paslaugų naudotojo teisės.",
            "Vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą.",
        ],
        "request": "Prašau išnagrinėti paslaugos teikimo problemą ir pateikti sprendimą.",
    },
    {
        "category": "Energijos tiekimas",
        "keywords": ["elektra", "dujos", "energija", "tiekėjas", "eso", "ignitis"],
        "document_title": "PRETENZIJA DĖL ENERGIJOS PASLAUGŲ",
        "recipient": "Energijos paslaugų teikėjas",
        "copy_key": "VERT",
        "laws": [
            "Lietuvos Respublikos energetikos įstatymas – vartotojų teisės energetikos sektoriuje.",
            "Vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą.",
        ],
        "request": "Prašau išnagrinėti ginčą dėl energijos paslaugų ir pateikti sprendimą.",
    },
    {
        "category": "Asmens duomenys",
        "keywords": ["asmens duomenys", "duomenys", "privatumas", "gdpr", "nutekino", "neteisėtai tvarko"],
        "document_title": "SKUNDAS DĖL ASMENS DUOMENŲ TVARKYMO",
        "recipient": "Duomenis tvarkanti įmonė / organizacija",
        "copy_key": "VDAI",
        "laws": [
            "Bendrasis duomenų apsaugos reglamentas (BDAR / GDPR).",
            "Lietuvos Respublikos asmens duomenų teisinės apsaugos įstatymas.",
        ],
        "request": "Prašau paaiškinti duomenų tvarkymo pagrindą ir pašalinti galimą pažeidimą.",
    },
    {
        "category": "Galimo sukčiavimo požymiai",
        "keywords": ["sukčiavimas", "apgavo", "scam", "fraud", "netikra", "melavo", "praradau pinigus"],
        "document_title": "PAREIŠKIMAS DĖL GALIMO SUKČIAVIMO",
        "recipient": "Policija",
        "copy_key": "POLICIJA",
        "laws": [
            "Lietuvos Respublikos baudžiamojo kodekso 182 straipsnis – sukčiavimas, kai yra tyčinio apgaulės būdu padarytos žalos požymių.",
            "Vartotojų teisių apsaugos įstatymas – jei ginčas susijęs su vartojimo santykiais.",
        ],
        "request": "Prašau įvertinti pateiktas aplinkybes ir pradėti tyrimą, jei bus nustatyti galimos nusikalstamos veikos požymiai.",
    },
]


def now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_case_id(chat_id: int) -> str:
    case = CASES.get(chat_id)
    if case and case.get("case_id"):
        return case["case_id"]
    return f"CASE-{datetime.now().year}-{str(len(CASES) + 1).zfill(6)}"


def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    if not TELEGRAM_API:
        logger.error("TELEGRAM_BOT_TOKEN missing")
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
        logger.error("Telegram send error: %s %s", r.status_code, r.text)
    return r.json() if r.ok else None


def answer_callback(callback_query_id: str, text: str = ""):
    if TELEGRAM_API:
        requests.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )


def start_menu():
    return {"inline_keyboard": [
        [{"text": "📄 Nauja byla", "callback_data": "new_case"}],
        [{"text": "❓ Užduoti klausimą", "callback_data": "ask_question"}],
        [{"text": "ℹ️ Kaip tai veikia", "callback_data": "how_it_works"}],
    ]}


def case_menu():
    return {"inline_keyboard": [
        [{"text": "⚖️ Teisinis vertinimas", "callback_data": "legal_review"}],
        [{"text": "📄 Generuoti dokumentą", "callback_data": "generate_document"}],
        [{"text": "🏛 Parinkti instituciją", "callback_data": "select_institution"}],
        [{"text": "🏠 Pagrindinis meniu", "callback_data": "main_menu"}],
    ]}


def back_menu():
    return {"inline_keyboard": [[{"text": "🏠 Pagrindinis meniu", "callback_data": "main_menu"}]]}


def classify_case(text: str) -> dict:
    t = (text or "").lower()
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
        "copy_key": "VVTAT",
        "laws": [
            "Lietuvos Respublikos vartotojų teisių apsaugos įstatymas – vartotojo teisė į teisėtų interesų gynimą.",
            "Lietuvos Respublikos civilinis kodeksas – vartojimo sutarčių vykdymas.",
        ],
        "request": "Prašau išnagrinėti situaciją ir pateikti sprendimą.",
    }


def institution_block(key: str) -> str:
    inst = INSTITUTIONS.get(key)
    if not inst:
        return "[Papildoma institucija, jei taikoma]"
    return f"{inst['name']}\n{inst['address']}\n{inst['email']}"


def extract_company_name(text: str) -> str:
    patterns = [
        r"(?:įmonė|pardavėjas|parduotuvė|paslaugų teikėjas)\s*[:\-]\s*([A-Za-zĄČĘĖĮŠŲŪŽąčęėįšųūž0-9\"„“\s\.\-]+)",
        r"(?:iš|pas)\s+(UAB\s+[A-Za-zĄČĘĖĮŠŲŪŽąčęėįšųūž0-9\"„“\s\.\-]+)",
    ]
    for p in patterns:
        m = re.search(p, text or "", re.IGNORECASE)
        if m:
            return m.group(1).strip()[:80]
    return ""


def clean_summary(user_text: str) -> str:
    text = " ".join((user_text or "").split())
    if len(text) > 520:
        text = text[:520].rsplit(" ", 1)[0] + "..."
    return text


def build_document(chat_id: int) -> str:
    case = CASES.get(chat_id, {})
    user_text = case.get("user_text", "")
    case_id = case.get("case_id", get_case_id(chat_id))
    rule = classify_case(user_text)
    company = extract_company_name(user_text)
    recipient = company if company else rule["recipient"]
    laws_text = "\n".join([f"- {law}" for law in rule["laws"]])

    return f"""
KAM:

{recipient}
[Adresas]
[El. paštas]

KOPIJA:

{institution_block(rule["copy_key"])}

NUO:

[Vardas Pavardė]
[Adresas]
[El. paštas]
[Telefonas]

BYLOS NUMERIS: {case_id}

{rule["document_title"]}

Sveiki,

Situacijos santrauka:

{clean_summary(user_text) if user_text else "[Trumpai aprašykite situaciją.]"}

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


def build_legal_preview(chat_id: int) -> str:
    user_text = CASES.get(chat_id, {}).get("user_text", "")
    rule = classify_case(user_text)
    laws = "\n".join([f"• {escape(law)}" for law in rule["laws"]])
    return (
        f"🔎 <b>Nustatyta kategorija:</b>\n{escape(rule['category'])}\n\n"
        f"📑 <b>Dokumentas:</b>\n{escape(rule['document_title'])}\n\n"
        f"🏛 <b>Adresatas:</b>\n{escape(rule['recipient'])}\n\n"
        f"📌 <b>Institucija / kopija:</b>\n{escape(institution_block(rule['copy_key']))}\n\n"
        f"⚖️ <b>Taikomi teisės aktai:</b>\n{laws}"
    )


def openai_short_assessment(user_text: str) -> str:
    if not OPENAI_API_KEY:
        return ""
    system_prompt = (
        "Tu esi Justice AI vartotojų teisių asistentas Lietuvoje. "
        "Atsakyk trumpai, aiškiai, lietuviškai. "
        "Neteik kategoriškų kaltinimų. "
        "Naudok formuluotę 'gali būti pažeistos vartotojo teisės'. "
        "Žodį 'normaliai' keisk į 'įprastai'. "
        "Klausimų turi būti ne daugiau kaip 3, pasiūlymų ne daugiau kaip 2."
    )
    user_prompt = (
        "Įvertink vartotojo situaciją ir pateik:\n"
        "1) trumpą situacijos santrauką,\n"
        "2) pirminį teisinį vertinimą iki 2 punktų,\n"
        "3) iki 3 klausimų bylai patikslinti,\n"
        "4) iki 2 praktinių pasiūlymų.\n\n"
        f"Situacija:\n{user_text}"
    )
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": OPENAI_MODEL,
                "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                "temperature": 0.2,
            },
            timeout=45,
        )
        if not r.ok:
            logger.error("OpenAI error: %s %s", r.status_code, r.text)
            return ""
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.exception("OpenAI request failed: %s", e)
        return ""


def show_start(chat_id: int):
    USER_STATES[chat_id] = "main_menu"
    text = (
        "⚖️ <b>Justice AI</b>\n\n"
        "Dirbtiniu intelektu paremtas vartotojų teisių asistentas.\n\n"
        "Padedu:\n"
        "• Įvertinti situaciją\n"
        "• Nustatyti galimus pažeidimus\n"
        "• Parinkti atsakingas institucijas\n"
        "• Parengti skundus ir pretenzijas\n"
        "• Sugeneruoti dokumentus\n\n"
        "Pasirinkite veiksmą:"
    )
    send_message(chat_id, text, reply_markup=start_menu())


def create_new_case(chat_id: int):
    case_id = f"CASE-{datetime.now().year}-{str(len(CASES) + 1).zfill(6)}"
    CASES[chat_id] = {"case_id": case_id, "user_text": "", "created_at": now_date()}
    USER_STATES[chat_id] = "waiting_case_description"
    text = (
        f"📁 <b>Sukurta nauja byla</b>\n\n"
        f"Bylos numeris: <b>{escape(case_id)}</b>\n\n"
        "Trumpai aprašykite situaciją.\n\n"
        "Pavyzdys:\n"
        "<i>Nusipirkau prekę internetu, bet jos negavau. Pardavėjas neatsako.</i>"
    )
    send_message(chat_id, text, reply_markup=back_menu())


def process_case_description(chat_id: int, text: str):
    case_id = CASES.get(chat_id, {}).get("case_id") or get_case_id(chat_id)
    CASES[chat_id] = {
        "case_id": case_id,
        "user_text": text,
        "created_at": CASES.get(chat_id, {}).get("created_at", now_date()),
        "updated_at": now_date(),
    }
    USER_STATES[chat_id] = "case_ready"
    rule = classify_case(text)
    company = extract_company_name(text)
    recipient = company if company else rule["recipient"]

    response = (
        f"📁 <b>Bylos numeris:</b> {escape(case_id)}\n\n"
        f"🔎 <b>Nustatyta kategorija:</b>\n{escape(rule['category'])}\n\n"
        f"🏛 <b>Adresatas:</b>\n{escape(recipient)}\n\n"
        f"📑 <b>Dokumento tipas:</b>\n{escape(rule['document_title'])}\n\n"
        "Pasirinkite veiksmą:"
    )
    send_message(chat_id, response, reply_markup=case_menu())


@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "Justice AI",
        "version": "updated-start-menu-2026-06-07",
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

            if data == "main_menu":
                show_start(chat_id)
            elif data == "new_case":
                create_new_case(chat_id)
            elif data == "ask_question":
                USER_STATES[chat_id] = "waiting_question"
                send_message(chat_id, "❓ Parašykite savo klausimą apie vartotojų teises.", reply_markup=back_menu())
            elif data == "how_it_works":
                send_message(
                    chat_id,
                    "ℹ️ <b>Kaip tai veikia</b>\n\n"
                    "1. Paspauskite „📄 Nauja byla“.\n"
                    "2. Trumpai aprašykite situaciją.\n"
                    "3. Justice AI nustatys kategoriją, instituciją ir dokumento tipą.\n"
                    "4. Galėsite sugeneruoti pretenziją arba skundą.\n\n"
                    "Dokumente automatiškai parenkami taikytini teisės aktai pagal situaciją.",
                    reply_markup=back_menu(),
                )
            elif data == "generate_document":
                if not CASES.get(chat_id, {}).get("user_text"):
                    send_message(chat_id, "Pirmiausia sukurkite bylą ir aprašykite situaciją.", reply_markup=start_menu())
                else:
                    send_message(chat_id, "⏳ Ruošiu dokumentą...")
                    send_message(chat_id, f"<pre>{escape(build_document(chat_id))}</pre>", reply_markup=case_menu())
            elif data == "select_institution":
                if not CASES.get(chat_id, {}).get("user_text"):
                    send_message(chat_id, "Pirmiausia sukurkite bylą ir aprašykite situaciją.", reply_markup=start_menu())
                else:
                    send_message(chat_id, build_legal_preview(chat_id), reply_markup=case_menu())
            elif data == "legal_review":
                user_text = CASES.get(chat_id, {}).get("user_text", "")
                if not user_text:
                    send_message(chat_id, "Pirmiausia sukurkite bylą ir aprašykite situaciją.", reply_markup=start_menu())
                else:
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
                    send_message(chat_id, escape(assessment), reply_markup=case_menu())

            return jsonify({"ok": True})

        if "message" in update:
            msg = update["message"]
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")

            if not chat_id:
                return jsonify({"ok": True})

            if text.startswith("/start"):
                show_start(chat_id)
                return jsonify({"ok": True})

            state = USER_STATES.get(chat_id)

            if state == "waiting_case_description":
                process_case_description(chat_id, text)
            elif state == "waiting_question":
                assessment = openai_short_assessment(text)
                if not assessment:
                    assessment = "Gali būti vartotojo teisių klausimas. Jei norite parengti dokumentą, pasirinkite „📄 Nauja byla“."
                send_message(chat_id, escape(assessment), reply_markup=start_menu())
                USER_STATES[chat_id] = "main_menu"
            else:
                send_message(chat_id, "Norėdami pradėti, pasirinkite „📄 Nauja byla“ arba naudokite meniu.", reply_markup=start_menu())

            return jsonify({"ok": True})

    except Exception as e:
        logger.exception("Webhook error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})


@app.route("/set-webhook", methods=["GET"])
def set_webhook():
    if not TELEGRAM_API:
        return jsonify({"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"}), 400

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if not public_base_url:
        return jsonify({"ok": False, "error": "PUBLIC_BASE_URL missing"}), 400

    webhook_url = public_base_url.rstrip("/") + "/telegram-webhook"
    r = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": webhook_url}, timeout=20)
    return jsonify(r.json())


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
