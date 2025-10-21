#!/usr/bin/env python3
"""
Bot Telegram para cobranças automáticas via Depix (Atlas DAO)
- Usa taxNumber fixo (12345678910)
- Mensagens substituíveis (não polui o chat)
- Botão "Já paguei" com verificação
- Reenvio automático a cada 2h no dia da cobrança
- Menu /start /pagar /status
"""

import os
import json
import logging
from datetime import datetime, time
from typing import Dict, Optional
import base64
import io
import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from PIL import Image

# ----------------- CONFIG -----------------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WALLET_ADDRESS = os.getenv(
    "WALLET_ADDRESS",
    "lq1qqw3nx0darshqqzl8t95j0vj3xxuwmp4a4fyz799plu7m8d4ztr2jugftryer3khq0jmskgppe6ughwyevgwmuvq8de75sgyy2",
)
ATLAS_API_KEY = os.getenv(
    "ATLAS_API_KEY",
    "atlas_ceaf6237e499f94dfe87ef62b19e25b360293369cbacfdf99760ee255761b5f5",
)
ATLAS_API_CREATE = "https://api.atlasdao.info/api/v1/external/pix/create"
ATLAS_API_STATUS = "https://api.atlasdao.info/api/v1/external/pix/status"
FIXED_TAX_NUMBER = "12345678910"

DATA_FILE = "usuarios.json"
user_states: Dict[int, str] = {}
last_message_id: Dict[int, int] = {}
paid_flags: Dict[int, bool] = {}
last_payment_id: Dict[int, str] = {}

# ----------------- CLIENTE MANAGER -----------------
class ClienteManager:
    def __init__(self):
        self.clientes = self.load()

    def load(self):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                try:
                    return json.load(f)
                except:
                    return {}
        return {}

    def save(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.clientes, f, indent=2)

    def add(self, user_id, username, dia, valor):
        self.clientes[str(user_id)] = {
            "username": username,
            "dia_pagamento": dia,
            "valor": valor,
            "ativo": True,
        }
        self.save()

    def get(self, user_id):
        return self.clientes.get(str(user_id))

    def get_clientes_do_dia(self, dia):
        return [
            (int(uid), c)
            for uid, c in self.clientes.items()
            if c["dia_pagamento"] == dia and c.get("ativo")
        ]


clientes_manager = ClienteManager()

# ----------------- UTIL -----------------
async def replace_message(context, chat_id, text=None, photo=None, markup=None):
    msg_id = last_message_id.get(chat_id)
    try:
        if msg_id:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

    if photo:
        sent = await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=markup, parse_mode="Markdown")
    else:
        sent = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=markup)

    last_message_id[chat_id] = sent.message_id

# ----------------- DEPAGOS -----------------
async def gerar_cobranca(user_id, username, valor, context, schedule_retries=False):
    """Baseada na versão antiga (funcional)"""
    payload = {
        "amount": round(float(valor), 2),
        "description": "Assinatura Mensal OMTB",
        "taxNumber": FIXED_TAX_NUMBER,
        "walletAddress": WALLET_ADDRESS,
    }

    headers = {"X-API-Key": ATLAS_API_KEY, "Content-Type": "application/json"}

    logger.info(f"➡️ POST {ATLAS_API_CREATE} {payload}")
    r = requests.post(ATLAS_API_CREATE, json=payload, headers=headers, timeout=30)

    if not r.ok:
        await replace_message(context, user_id, f"❌ Erro ao gerar cobrança.\n\nCódigo: {r.status_code}\n{r.text}")
        return

    data = r.json()
    qr_code = data.get("qrCode")
    qr_image = data.get("qrCodeImage")
    pid = data.get("id")
    last_payment_id[user_id] = pid
    paid_flags[user_id] = False

    mensagem = (
        f"📃 *Informações de pagamento*\n"
        f"💰 Valor: R$ {valor:.2f}\n\n"
        f"🔑 *Chave PIX (Copia e Cola)*\n"
        f"```\n{qr_code}\n```\n\n"
        f"⏰ Expira em 30 minutos.\n"
        f"_Cobrança Depix não reembolsável_"
    )

    btns = [[InlineKeyboardButton("✅ Já paguei", callback_data=f"verificar_{pid}")]]
    markup = InlineKeyboardMarkup(btns)

    # imagem como antes
    if qr_image and "," in qr_image:
        qr_image = qr_image.split(",")[1]
    try:
        img_data = base64.b64decode(qr_image)
        img = Image.open(io.BytesIO(img_data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        await replace_message(context, user_id, mensagem, photo=buf, markup=markup)
    except Exception as e:
        logger.error(f"Erro ao enviar imagem: {e}")
        await replace_message(context, user_id, mensagem, markup=markup)

    # Reagendar se for job do dia
    if schedule_retries:
        name = f"retry_{user_id}"
        for j in context.job_queue.get_jobs_by_name(name):
            j.schedule_removal()
        context.job_queue.run_repeating(retry_cobranca, 2 * 60 * 60, first=2 * 60 * 60, name=name, data={"user_id": user_id})

async def retry_cobranca(context):
    uid = context.job.data["user_id"]
    cliente = clientes_manager.get(uid)
    if not cliente:
        context.job.schedule_removal()
        return
    if paid_flags.get(uid):
        context.job.schedule_removal()
        return
    await gerar_cobranca(uid, cliente["username"], cliente["valor"], context)

async def verificar_pagamento(payment_id):
    try:
        r = requests.get(f"{ATLAS_API_STATUS}/{payment_id}", headers={"X-API-Key": ATLAS_API_KEY}, timeout=15)
        if not r.ok:
            return False
        return r.json().get("status") == "PAID"
    except Exception:
        return False

# ----------------- FLUXO -----------------
async def start(update, context):
    user = update.effective_user
    user_states[user.id] = "day"
    await replace_message(context, user.id, f"Bem-vindo, *{user.first_name}*!\n\n📅 Qual dia do mês deseja pagar?")

async def receber_dia(update, context):
    try:
        dia = int(update.message.text.strip())
        if not 1 <= dia <= 31:
            await replace_message(context, update.effective_user.id, "Digite um dia válido (1–31).")
            return
        context.user_data["dia"] = dia
        user_states[update.effective_user.id] = "amount"
        await replace_message(context, update.effective_user.id, "💵 Qual o valor (até 3000)?")
    except:
        await replace_message(context, update.effective_user.id, "Digite apenas números.")

async def receber_valor(update, context):
    try:
        valor = float(update.message.text.replace(",", "."))
        if not (0 < valor <= 3000):
            await replace_message(context, update.effective_user.id, "Valor inválido.")
            return
        user = update.effective_user
        dia = context.user_data.get("dia")
        clientes_manager.add(user.id, user.first_name, dia, valor)
        await replace_message(context, user.id, f"✅ Configurado!\nDia: *{dia}*\nValor: *R$ {valor:.2f}*")

        if datetime.now().day == dia:
            await replace_message(context, user.id, "⏳ Gerando cobrança...")
            await gerar_cobranca(user.id, user.first_name, valor, context, schedule_retries=True)
    except:
        await replace_message(context, update.effective_user.id, "Erro ao processar valor.")

async def handle_text(update, context):
    estado = user_states.get(update.effective_user.id)
    if estado == "day":
        await receber_dia(update, context)
    elif estado == "amount":
        await receber_valor(update, context)
    else:
        await replace_message(context, update.effective_user.id, "Use /start para configurar.")

# ----------------- CALLBACK -----------------
async def verificar_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    pid = query.data.replace("verificar_", "")
    pago = await verificar_pagamento(pid)
    if pago:
        paid_flags[uid] = True
        for j in context.job_queue.get_jobs_by_name(f"retry_{uid}"):
            j.schedule_removal()
        await replace_message(context, uid, "✅ *Pagamento confirmado!*\n\nObrigado! 🎉")
    else:
        await replace_message(
            context,
            uid,
            "❌ *Pagamento não localizado.*\n\n"
            "Se isso for um erro, envie seu comprovante ao suporte.\n"
            "Caso ainda não tenha pago, efetue e clique novamente em *Já paguei*.",
        )

# ----------------- COMANDOS -----------------
async def pagar(update, context):
    uid = update.effective_user.id
    cliente = clientes_manager.get(uid)
    if not cliente:
        await replace_message(context, uid, "Use /start para configurar primeiro.")
        return
    await replace_message(context, uid, "⏳ Gerando cobrança...")
    await gerar_cobranca(uid, cliente["username"], cliente["valor"], context)

async def status(update, context):
    cliente = clientes_manager.get(update.effective_user.id)
    if not cliente:
        await replace_message(context, update.effective_user.id, "Você ainda não está configurado. Use /start.")
        return
    txt = (
        f"📊 *Seu cadastro*\n"
        f"- Dia: *{cliente['dia_pagamento']}*\n"
        f"- Valor: *R$ {cliente['valor']:.2f}*\n"
    )
    await replace_message(context, update.effective_user.id, txt)

# ----------------- MENU -----------------
async def _register_bot_commands(app: Application):
    cmds = [
        BotCommand("start", "Configurar cobrança"),
        BotCommand("pagar", "Gerar cobrança agora"),
        BotCommand("status", "Ver status"),
    ]
    await app.bot.set_my_commands(cmds)

async def _post_init_register_menu(app: Application):
    await _register_bot_commands(app)

# ----------------- MAIN -----------------
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN não configurado!")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pagar", pagar))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(verificar_callback))
    app.post_init = _post_init_register_menu
    logger.info("🤖 Bot iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
