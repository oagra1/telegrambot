#!/usr/bin/env python3
"""
Bot Telegram para cobranças automáticas via Depix (Atlas DAO)
- Banner único (QR + texto) numa imagem
- Botão "Já paguei" com verificação + "Voltar para opção anterior"
- Solicita CPF/CNPJ apenas se necessário (ordem: env -> cadastro -> perguntar)
- Mensagens se substituem (não polui o chat)
- Reenvio automático a cada 2h no dia da cobrança até confirmar pagamento
- Menu nativo do Telegram (/start, /pagar, /status)
"""

import os
import json
import logging
from datetime import datetime, time
from typing import Dict, Optional
import base64
import io

try:
    import requests
except ImportError:
    print("ERRO: requests não instalado!")
    raise

try:
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
except ImportError as e:
    print(f"ERRO: python-telegram-bot não instalado! {e}")
    raise

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("AVISO: Pillow não instalado - QR Codes não funcionarão")

# --------------------------------- Logging ---------------------------------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

print("=" * 50)
print("🚀 INICIANDO BOT...")
print("=" * 50)

# --------------------------------- Config ----------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WALLET_ADDRESS = os.getenv(
    "WALLET_ADDRESS",
    "lq1qqw3nx0darshqqzl8t95j0vj3xxuwmp4a4fyz799plu7m8d4ztr2jugftryer3khq0jmskgppe6ughwyevgwmuvq8de75sgyy2",
)
ATLAS_API_KEY = os.getenv("ATLAS_API_KEY", "atlas_ceaf6237e499f94dfe87ef62b19e25b360293369cbacfdf99760ee255761b5f5")
ATLAS_API_CREATE = "https://api.atlasdao.info/api/v1/external/pix/create"
ATLAS_API_STATUS = "https://api.atlasdao.info/api/v1/external/pix/status"
ATLAS_TAX_NUMBER_DEFAULT = os.getenv("ATLAS_TAX_NUMBER_DEFAULT", "").strip()

print(f"✅ Token Telegram: {'OK' if TELEGRAM_TOKEN else '❌ FALTANDO'}")
print(f"✅ Wallet Address: {WALLET_ADDRESS[:20]}...")
print("✅ API Key: OK")
print(f"ℹ️ Tax default: {'definido' if ATLAS_TAX_NUMBER_DEFAULT else 'não definido'}")
print("-" * 50)

DATA_FILE = "usuarios.json"
user_states: Dict[int, str] = {}  # 'day' | 'amount' | 'tax'

# Estado de mensagens/controle
last_message_id: Dict[int, int] = {}   # última msg enviada (pra substituir)
last_payment_id: Dict[int, str] = {}   # último payment id
paid_flags: Dict[int, bool] = {}       # pagou no ciclo

# --------------------------- Helpers ---------------------------------------
def sanitize_tax_number(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def valid_tax_number(s: str) -> bool:
    return len(s) in (11, 14)

def formatar_valor_brl(valor: float) -> str:
    return f"{valor:.2f}"

def try_load_font(size: int):
    for name in ["Inter.ttf", "Poppins-Regular.ttf", "arial.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()

# --------------------------- Cliente Manager -------------------------------
class ClienteManager:
    def __init__(self):
        self.clientes = self.load_data()

    def load_data(self) -> Dict:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Erro ao carregar JSON: {e}")
        return {}

    def save_data(self):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(self.clientes, f, indent=2)
        except Exception as e:
            logger.error(f"Erro ao salvar JSON: {e}")

    def add_or_update(self, user_id: int, username: str, dia: int, valor: float, tax_number: Optional[str] = None):
        if tax_number:
            tax_number = sanitize_tax_number(tax_number)
        self.clientes[str(user_id)] = {
            "username": username,
            "dia_pagamento": dia,
            "valor": valor,
            "tax_number": tax_number or self.clientes.get(str(user_id), {}).get("tax_number"),
            "ativo": True,
        }
        self.save_data()

    def set_tax(self, user_id: int, tax_number: str):
        c = self.clientes.get(str(user_id)) or {}
        c["tax_number"] = sanitize_tax_number(tax_number)
        self.clientes[str(user_id)] = c
        self.save_data()

    def get(self, user_id: int):
        return self.clientes.get(str(user_id))

    def get_clientes_do_dia(self, dia: int):
        return [
            (int(uid), c) for uid, c in self.clientes.items()
            if c.get("dia_pagamento") == dia and c.get("ativo")
        ]

clientes_manager = ClienteManager()

# ------------------------- Banner (imagem única) ---------------------------
def build_banner(qr_image_b64: str, valor_formatado: str, qr_string: str) -> bytes:
    """Gera imagem única (banner) com visual estilo card + QR + textos dentro."""
    if "," in qr_image_b64:
        qr_image_b64 = qr_image_b64.split(",", 1)[1]
    qr_img = Image.open(io.BytesIO(base64.b64decode(qr_image_b64))).convert("RGBA")

    # Canvas
    W = max(900, qr_img.width + 80)
    H = qr_img.height + 340
    banner = Image.new("RGBA", (W, H), (10, 14, 18, 255))

    draw = ImageDraw.Draw(banner)
    font_title = try_load_font(48)
    font_text  = try_load_font(30)
    font_small = try_load_font(24)

    # Cabeçalho
    draw.rounded_rectangle((20, 20, W - 20, 220), radius=28, fill=(20, 28, 36, 255))
    draw.text((50, 50), "DePix", fill=(72, 244, 122, 255), font=font_title)
    draw.text((50, 110), "Rede: Liquid", fill=(180, 200, 210, 255), font=font_text)
    draw.text((W - 320, 50), "Envie o endereço | Send address", fill=(160, 200, 255, 255), font=font_small)

    # Bloco de informações
    info_top = 240
    draw.rounded_rectangle((20, info_top, W - 20, info_top + 260), radius=28, fill=(16, 22, 28, 255))
    draw.text((50, info_top + 20), "📄 Informações de compra:", fill=(230, 240, 245, 255), font=font_text)
    draw.text((50, info_top + 70), f"Valor: R$ {valor_formatado}", fill=(200, 210, 220, 255), font=font_text)
    draw.text((50, info_top + 120), "⏰ Expira em 30 minutos", fill=(220, 190, 60, 255), font=font_text)

    # PIX copia-e-cola (trunc para caber)
    clip = qr_string if len(qr_string) <= 80 else (qr_string[:77] + "...")
    draw.text((50, info_top + 170), f"🔑 Chave PIX (copia e cola): {clip}",
              fill=(160, 175, 185, 255), font=font_small)

    # Posiciona o QR centralizado embaixo
    qr_x = (W - qr_img.width) // 2
    qr_y = info_top + 280
    banner.paste(qr_img, (qr_x, qr_y), qr_img)

    buf = io.BytesIO()
    banner.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()

# -------------------- Envio substituindo mensagem --------------------------
async def send_or_replace_text(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str,
                               reply_markup: Optional[InlineKeyboardMarkup] = None):
    msg_id = last_message_id.get(chat_id)
    try:
        if msg_id:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass
    m = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=reply_markup)
    last_message_id[chat_id] = m.message_id

async def send_or_replace_photo(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    photo_bytes: bytes,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
):
    msg_id = last_message_id.get(chat_id)
    bio = io.BytesIO(photo_bytes); bio.name = "banner.png"
    try:
        if msg_id:
            await context.bot.edit_message_media(
                chat_id=chat_id,
                message_id=msg_id,
                media=InputMediaPhoto(bio, caption=caption),
                reply_markup=reply_markup,
            )
            return
    except Exception:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    sent = await context.bot.send_photo(chat_id=chat_id, photo=bio, caption=caption, reply_markup=reply_markup)
    last_message_id[chat_id] = sent.message_id

# ---------------------------- API Atlas ------------------------------------
async def verificar_pagamento(payment_id: str) -> Dict:
    try:
        url = f"{ATLAS_API_STATUS}/{payment_id}"
        headers = {"X-API-Key": ATLAS_API_KEY}
        r = requests.get(url, headers=headers, timeout=15)
        if not r.ok:
            return {"success": False, "error": r.text}
        data = r.json()
        return {"success": True, "paid": data.get("status") == "PAID", "data": data}
    except Exception as e:
        logger.error(f"verificar_pagamento erro: {e}")
        return {"success": False, "error": str(e)}

def _resolve_tax_number(user_id: int) -> Optional[str]:
    """Ordem: env default -> cadastro -> None"""
    if ATLAS_TAX_NUMBER_DEFAULT:
        return sanitize_tax_number(ATLAS_TAX_NUMBER_DEFAULT)
    cli = clientes_manager.get(user_id)
    if cli and cli.get("tax_number"):
        return sanitize_tax_number(cli["tax_number"])
    return None

async def _ask_tax_number(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Pede CPF/CNPJ com botão de voltar."""
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar para opção anterior", callback_data="voltar_amount")]])
    user_states[user_id] = "tax"
    await send_or_replace_text(
        context, user_id,
        "Por favor, informe seu *CPF ou CNPJ* (somente números).",
        reply_markup=kb
    )

async def gerar_cobranca(
    user_id: int,
    username: str,
    valor: float,
    context: ContextTypes.DEFAULT_TYPE,
    schedule_retries: bool = False,
) -> Optional[str]:
    """Cria cobrança, envia banner (foto + botões), substitui mensagem anterior e retorna payment_id."""
    valor_formatado = round(float(valor), 2)

    # Garante taxNumber: se não houver, pede ao usuário
    tax_number = _resolve_tax_number(user_id)
    if not tax_number:
        await _ask_tax_number(user_id, context)
        return None

    payload = {
        "amount": valor_formatado,
        "description": "Assinatura Mensal OMTB",
        "walletAddress": WALLET_ADDRESS,
        "taxNumber": tax_number,
    }

    headers = {"X-API-Key": ATLAS_API_KEY, "Content-Type": "application/json"}
    logger.info(f"➡️ POST {ATLAS_API_CREATE} payload={payload}")

    try:
        resp = requests.post(ATLAS_API_CREATE, json=payload, headers=headers, timeout=30)
        logger.info(f"⬅️ {resp.status_code} {resp.text[:500]}")
        if not resp.ok:
            await send_or_replace_text(context, user_id,
                f"❌ Erro ao gerar cobrança.\n\nCódigo: {resp.status_code}\n{resp.text}")
            return None

        data = resp.json()
        payment_id = data.get("id")
        qr_string  = data.get("qrCode")
        qr_image   = data.get("qrCodeImage")

        if not qr_image or not qr_string or not payment_id:
            await send_or_replace_text(context, user_id, "❌ Resposta inválida da API.")
            return None

        # Gera banner único
        banner_bytes = build_banner(qr_image, formatar_valor_brl(valor_formatado), qr_string)
        caption = " "  # info está na imagem

        # teclado: Já paguei + Voltar
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Já paguei", callback_data=f"verificar_{payment_id}")],
            [InlineKeyboardButton("🔙 Voltar para opção anterior", callback_data="voltar_day")],
        ])

        await send_or_replace_photo(context, user_id, banner_bytes, caption, kb)

        last_payment_id[user_id] = payment_id
        paid_flags[user_id] = False

        # Agenda reenvio a cada 2h no dia da cobrança (se solicitado)
        if schedule_retries and context.job_queue:
            name = f"retry_{user_id}"
            for j in context.job_queue.get_jobs_by_name(name):
                j.schedule_removal()
            context.job_queue.run_repeating(
                callback=retry_cobranca_job,
                interval=2 * 60 * 60,   # 2h
                first=2 * 60 * 60,
                name=name,
                data={"user_id": user_id},
            )

        return payment_id

    except Exception as e:
        logger.exception("Erro em gerar_cobranca")
        await send_or_replace_text(context, user_id, f"❌ Erro interno ao gerar cobrança: {e}")
        return None

# --------------------------- Fluxo de conversa ------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_states[user.id] = "day"
    await send_or_replace_text(
        context, user.id,
        f"Bem-vindo, *{user.first_name}*!\n\n📅 Qual dia do mês você deseja pagar?"
    )

async def receber_dia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        dia = int(update.message.text.strip())
        if not 1 <= dia <= 31:
            await send_or_replace_text(context, update.effective_user.id, "Digite um dia entre 1 e 31.")
            return
        context.user_data["dia"] = dia
        user_states[update.effective_user.id] = "amount"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar para opção anterior", callback_data="voltar_day")]])
        await send_or_replace_text(context, update.effective_user.id, "💵 Qual o valor (até 3000)?", reply_markup=kb)
    except ValueError:
        await send_or_replace_text(context, update.effective_user.id, "Digite apenas números.")

async def receber_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valor = float(update.message.text.replace(",", "."))
        if not (0 < valor <= 3000):
            await send_or_replace_text(context, update.effective_user.id, "Valor inválido.")
            return

        user = update.effective_user
        dia = context.user_data.get("dia")
        username = user.first_name or user.username or f"User{user.id}"
        # salva sem tax por enquanto
        clientes_manager.add_or_update(user.id, username, dia, valor)

        # Se hoje é o dia, já inicia a cobrança (vai pedir tax se faltar)
        user_states[user.id] = None
        await send_or_replace_text(context, user.id, "⏳ Gerando cobrança...")
        await gerar_cobranca(user.id, username, valor, context, schedule_retries=True)

    except ValueError:
        await send_or_replace_text(context, update.effective_user.id, "Digite apenas números.")

async def receber_tax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recebe CPF/CNPJ quando necessário."""
    user_id = update.effective_user.id
    tax = sanitize_tax_number(update.message.text)
    if not valid_tax_number(tax):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar para opção anterior", callback_data="voltar_amount")]])
        await send_or_replace_text(context, user_id, "Documento inválido. Envie CPF (11) ou CNPJ (14).", reply_markup=kb)
        user_states[user_id] = "tax"
        return

    clientes_manager.set_tax(user_id, tax)
    user_states[user_id] = None

    # Retoma a cobrança
    cli = clientes_manager.get(user_id)
    await send_or_replace_text(context, user_id, "⏳ Gerando cobrança...")
    await gerar_cobranca(user_id, cli["username"], cli["valor"], context, schedule_retries=True)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    estado = user_states.get(update.effective_user.id)
    if estado == "day":
        await receber_dia(update, context)
    elif estado == "amount":
        await receber_valor(update, context)
    elif estado == "tax":
        await receber_tax(update, context)
    else:
        await send_or_replace_text(context, update.effective_user.id, "Use /start para iniciar.")

# ------------------------------ Callbacks -----------------------------------
async def verificar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_text = query.data
    user_id = query.from_user.id

    # Voltar
    if data_text in {"voltar_day", "voltar_amount"}:
        if data_text == "voltar_amount":
            # volta para a pergunta do valor
            user_states[user_id] = "amount"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar para opção anterior", callback_data="voltar_day")]])
            await send_or_replace_text(context, user_id, "💵 Qual o valor (até 3000)?", reply_markup=kb)
        else:
            # volta para a pergunta do dia
            user_states[user_id] = "day"
            await send_or_replace_text(context, user_id, "📅 Qual dia do mês você deseja pagar?")
        return

    if data_text.startswith("verificar_"):
        payment_id = data_text.replace("verificar_", "")
        await send_or_replace_text(context, user_id, "⏳ Verificando pagamento na rede...")

        res = await verificar_pagamento(payment_id)

        if res.get("success") and res.get("paid"):
            paid_flags[user_id] = True
            if context.job_queue:
                for j in context.job_queue.get_jobs_by_name(f"retry_{user_id}"):
                    j.schedule_removal()
            await send_or_replace_text(
                context,
                user_id,
                "✅ *Pagamento confirmado!*\n\nObrigado! Sua próxima cobrança virá no mês seguinte.",
            )
        else:
            texto = ("❌ *Pagamento não localizado.*\n\n"
                     "Se isso for um erro, contate o suporte e envie seu comprovante.\n"
                     "Caso não tenha efetuado o pagamento, realize-o e toque novamente em *Já paguei*.")
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Já paguei", callback_data=f"verificar_{payment_id}")],
                 [InlineKeyboardButton("🔙 Voltar para opção anterior", callback_data="voltar_day")]]
            )
            msg_id = last_message_id.get(user_id)
            try:
                if msg_id:
                    await context.bot.edit_message_caption(
                        chat_id=user_id, message_id=msg_id, caption=texto, reply_markup=kb, parse_mode="Markdown"
                    )
                else:
                    await send_or_replace_text(context, user_id, texto, reply_markup=kb)
            except Exception:
                await send_or_replace_text(context, user_id, texto, reply_markup=kb)

# ------------------------------- Comandos -----------------------------------
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra status do cadastro e próxima cobrança provável."""
    cliente = clientes_manager.get(update.effective_user.id)
    if not cliente:
        await send_or_replace_text(context, update.effective_user.id, "Você ainda não está configurado. Use /start.")
        return

    hoje = datetime.now()
    dia = cliente["dia_pagamento"]
    proximo_mes = hoje.month if hoje.day <= dia else (1 if hoje.month == 12 else hoje.month + 1)
    ano = hoje.year + (1 if (hoje.month == 12 and hoje.day > dia) else 0)
    txt = (
        f"📊 *Seu cadastro*\n"
        f"- Dia da cobrança: *{dia}*\n"
        f"- Valor: *R$ {cliente['valor']:.2f}*\n"
        f"- Próxima cobrança: *{dia:02d}/{proximo_mes:02d}/{ano}*\n"
        f"- CPF/CNPJ: {'cadastrado' if cliente.get('tax_number') else 'não cadastrado'}"
    )
    await send_or_replace_text(context, update.effective_user.id, txt)

async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pagar – gera uma cobrança imediata (substituindo mensagens)."""
    user_id = update.effective_user.id
    cliente = clientes_manager.get(user_id)
    if not cliente:
        await send_or_replace_text(context, user_id, "Você ainda não está configurado. Use /start.")
        return

    await send_or_replace_text(context, user_id, "⏳ Gerando cobrança...")
    await gerar_cobranca(user_id, cliente["username"], cliente["valor"], context, schedule_retries=True)

# ------------------------------ Jobs ---------------------------------------
async def retry_cobranca_job(context: ContextTypes.DEFAULT_TYPE):
    """Job a cada 2h: se ainda não pago, reenvia a cobrança (novo QR)."""
    user_id = context.job.data["user_id"]
    cliente = clientes_manager.get(user_id)
    if not cliente:
        context.job.schedule_removal()
        return

    if paid_flags.get(user_id):
        context.job.schedule_removal()
        return

    pid = last_payment_id.get(user_id)
    if pid:
        res = await verificar_pagamento(pid)
        if res.get("success") and res.get("paid"):
            paid_flags[user_id] = True
            await send_or_replace_text(context, user_id, "✅ *Pagamento confirmado!*\n\nObrigado! 🎉")
            context.job.schedule_removal()
            return

    await gerar_cobranca(
        user_id,
        cliente["username"],
        cliente["valor"],
        context,
        schedule_retries=False,  # job já existe
    )

async def preparar_cobrancas_do_dia(context: ContextTypes.DEFAULT_TYPE):
    """Roda diariamente às 8h e inicia (se necessário) as cobranças de quem vence hoje."""
    hoje = datetime.now().day
    for uid, dados in clientes_manager.get_clientes_do_dia(hoje):
        name = f"retry_{uid}"
        if context.job_queue.get_jobs_by_name(name):
            continue
        await gerar_cobranca(uid, dados["username"], dados["valor"], context, schedule_retries=True)

# ------------------------------ Menu (botão) --------------------------------
async def _register_bot_commands(app: Application):
    commands = [
        BotCommand("start", "Configurar cobrança"),
        BotCommand("pagar", "Gerar cobrança agora"),
        BotCommand("status", "Ver meu status"),
    ]
    await app.bot.set_my_commands(commands)

async def _post_init_register_menu(app: Application):
    await _register_bot_commands(app)

# ------------------------------ MAIN ---------------------------------------
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN não configurado!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pagar", pagar))
    app.add_handler(CommandHandler("status", status))

    # Texto genérico e callbacks
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(verificar_callback))

    # Menu do Telegram
    app.post_init = _post_init_register_menu

    # Job diário (8h) para iniciar as cobranças de quem vence hoje
    if app.job_queue:
        app.job_queue.run_daily(preparar_cobrancas_do_dia, time=time(hour=8, minute=0, second=0))

    logger.info("🤖 Bot iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

