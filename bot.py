#!/usr/bin/env python3
"""
Bot Telegram para cobranças automáticas via Depix (Atlas DAO)
- Banner único (QR + texto) numa imagem
- Botão "Já paguei" com verificação (mensagens se substituem)
- Sem solicitar CPF ao usuário (taxNumber omitido; usa ATLAS_TAX_NUMBER_DEFAULT se existir)
- Reenvio automático a cada 2h no dia da cobrança até confirmar pagamento
"""

import os
import json
import logging
from datetime import datetime, time, timedelta
from typing import Dict, Optional
import base64
import io

try:
    import requests
except ImportError:
    print("ERRO: requests não instalado!")
    raise

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
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

# Se a API exigir taxNumber, defina no ambiente e NÃO perguntaremos ao usuário
ATLAS_TAX_NUMBER_DEFAULT = os.getenv("ATLAS_TAX_NUMBER_DEFAULT", "").strip()

print(f"✅ Token Telegram: {'OK' if TELEGRAM_TOKEN else '❌ FALTANDO'}")
print(f"✅ Wallet Address: {WALLET_ADDRESS[:20]}...")
print("✅ API Key: OK")
if ATLAS_TAX_NUMBER_DEFAULT:
    print("✅ TaxNumber default ativo (sem perguntar ao usuário)")
print("-" * 50)

DATA_FILE = "usuarios.json"
user_states: Dict[int, str] = {}

# Mensagem mais recente por usuário (para substituir e não poluir o chat)
last_message_id: Dict[int, int] = {}
# Último payment_id por usuário (para verificação e reenvio)
last_payment_id: Dict[int, str] = {}
# Flag “pago” por ciclo mensal
paid_flags: Dict[int, bool] = {}

# --------------------------- Helpers ---------------------------------------
def limpar_cpf_cnpj(documento: str) -> str:
    return "".join(filter(str.isdigit, documento or ""))


def formatar_valor_brl(valor: float) -> str:
    return f"{valor:.2f}"


def try_load_font(size: int):
    # Tentativas de fontes comuns; se falhar, Pillow usa fonte padrão
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

    def add_or_update(self, user_id: int, username: str, dia: int, valor: float):
        self.clientes[str(user_id)] = {
            "username": username,
            "dia_pagamento": dia,
            "valor": valor,
            "ativo": True,
        }
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
    font_text = try_load_font(30)
    font_small = try_load_font(24)

    # Header com “DePix” e etiqueta “Rede: Liquid”
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

async def gerar_cobranca(
    user_id: int,
    username: str,
    valor: float,
    context: ContextTypes.DEFAULT_TYPE,
    schedule_retries: bool = False,
) -> Optional[str]:
    """Cria cobrança, envia banner (foto + botão), substitui mensagem anterior e retorna payment_id."""
    valor_formatado = round(float(valor), 2)

    payload = {
        "amount": valor_formatado,
        "description": "Assinatura Mensal OMTB",
        "walletAddress": WALLET_ADDRESS,
    }
    # inclui taxNumber apenas se definido por env (sem perguntar ao usuário)
    if ATLAS_TAX_NUMBER_DEFAULT:
        payload["taxNumber"] = ATLAS_TAX_NUMBER_DEFAULT

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
        qr_string = data.get("qrCode")
        qr_image = data.get("qrCodeImage")

        if not qr_image or not qr_string or not payment_id:
            await send_or_replace_text(context, user_id, "❌ Resposta inválida da API.")
            return None

        # Gera banner único
        banner_bytes = build_banner(qr_image, formatar_valor_brl(valor_formatado), qr_string)
        caption = " "  # caption mínima; toda info já está dentro da imagem

        # teclado com “Já paguei”
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Já paguei", callback_data=f"verificar_{payment_id}")]]
        )

        # Substitui mensagem anterior (se houver) por foto
        await send_or_replace_photo(context, user_id, banner_bytes, caption, kb)

        last_payment_id[user_id] = payment_id
        paid_flags[user_id] = False

        # Agenda reenvio a cada 2h no dia da cobrança (se solicitado)
        if schedule_retries:
            name = f"retry_{user_id}"
            # remove job antigo (se existir) antes de criar outro
            for j in context.job_queue.get_jobs_by_name(name):
                j.schedule_removal()
            context.job_queue.run_repeating(
                callback=retry_cobranca_job,
                interval=2 * 60 * 60,
                first=2 * 60 * 60,
                name=name,
                data={"user_id": user_id},
            )

        return payment_id

    except Exception as e:
        logger.exception("Erro em gerar_cobranca")
        await send_or_replace_text(context, user_id, f"❌ Erro interno ao gerar cobrança: {e}")
        return None

# -------------------- Envio substituindo mensagem --------------------------
async def send_or_replace_text(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    # apaga a última mensagem, se existir
    msg_id = last_message_id.get(chat_id)
    try:
        if msg_id:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass
    m = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    last_message_id[chat_id] = m.message_id

async def send_or_replace_photo(context: ContextTypes.DEFAULT_TYPE, chat_id: int, photo_bytes: bytes, caption: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    msg_id = last_message_id.get(chat_id)
    bio = io.BytesIO(photo_bytes); bio.name = "banner.png"
    try:
        if msg_id:
            # tenta editar a mídia; se falhar, deleta e manda de novo
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

# ------------------------------ Jobs ---------------------------------------
async def retry_cobranca_job(context: ContextTypes.DEFAULT_TYPE):
    """Job a cada 2h: se ainda não pago, reenvia a cobrança (novo QR)."""
    user_id = context.job.data["user_id"]
    cliente = clientes_manager.get(user_id)
    if not cliente:
        # nada pra fazer
        context.job.schedule_removal()
        return

    # Se já pago, encerra o job
    if paid_flags.get(user_id):
        context.job.schedule_removal()
        return

    # Se houver payment_id, primeiro verifica
    pid = last_payment_id.get(user_id)
    if pid:
        res = await verificar_pagamento(pid)
        if res.get("success") and res.get("paid"):
            paid_flags[user_id] = True
            # Confirma visualmente e para o job
            kb = None
            await send_or_replace_text(context, user_id, "✅ *Pagamento confirmado!*\n\nObrigado! 🎉")
            context.job.schedule_removal()
            return

    # Ainda não pago → gera uma nova cobrança (novo payment_id / novo QR)
    await gerar_cobranca(
        user_id,
        clientes_manager.get(user_id)["username"],
        clientes_manager.get(user_id)["valor"],
        context,
        schedule_retries=False,  # job já existe
    )

# --------------------------- Fluxo de conversa ------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_states[user.id] = "day"
    await send_or_replace_text(context, user.id,
        f"Bem-vindo, *{user.first_name}*!\n\n📅 Qual dia do mês você deseja pagar?")

async def receber_dia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        dia = int(update.message.text.strip())
        if not 1 <= dia <= 31:
            await send_or_replace_text(context, update.effective_user.id, "Digite um dia entre 1 e 31.")
            return
        context.user_data["dia"] = dia
        user_states[update.effective_user.id] = "amount"
        await send_or_replace_text(context, update.effective_user.id, "💵 Qual o valor (até 3000)?")
    except ValueError:
        await send_or_replace_text(context, update.effective_user.id, "Digite apenas números.")

async def receber_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valor = float(update.message.text.replace(",", "."))
        if not (0 < valor <= 3000):
            await send_or_replace_text(context, update.effective_user.id, "Valor inválido.")
            return
        # Salva cadastro
        user = update.effective_user
        dia = context.user_data.get("dia")
        username = user.first_name or user.username or f"User{user.id}"
        clientes_manager.add_or_update(user.id, username, dia, valor)
        user_states[user.id] = None

        await send_or_replace_text(
            context, user.id,
            f"✅ Configurado!\nDia: *{dia}*\nValor: *R$ {formatar_valor_brl(valor)}*"
        )

        # Se hoje é o dia, já cobra
        if datetime.now().day == dia:
            await send_or_replace_text(context, user.id, "⏳ Gerando cobrança...")
            await gerar_cobranca(user.id, username, valor, context, schedule_retries=True)

    except ValueError:
        await send_or_replace_text(context, update.effective_user.id, "Digite apenas números.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    estado = user_states.get(update.effective_user.id)
    if estado == "day":
        await receber_dia(update, context)
    elif estado == "amount":
        await receber_valor(update, context)
    else:
        await send_or_replace_text(context, update.effective_user.id, "Use /start para iniciar.")

# ------------------------------ Callbacks -----------------------------------
async def verificar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_text = query.data
    user_id = query.from_user.id

    if data_text.startswith("verificar_"):
        payment_id = data_text.replace("verificar_", "")
        res = await verificar_pagamento(payment_id)

        if res.get("success") and res.get("paid"):
            paid_flags[user_id] = True
            # cancela o job de retry, se houver
            for j in context.job_queue.get_jobs_by_name(f"retry_{user_id}"):
                j.schedule_removal()

            # substitui mensagem por confirmação (sem botão)
            await send_or_replace_text(context, user_id, "✅ *Pagamento confirmado!*\n\nObrigado! 🎉")
        else:
            # Não pago → mantém botão, com instruções
            texto = ("⚠️ *Pagamento não localizado.*\n\n"
                     "Se isso for um erro, contate o suporte e envie seu comprovante.\n"
                     "Caso não tenha efetuado o pagamento, realize-o e toque novamente no botão.")
            # Reanexa o mesmo botão de verificação
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Já paguei", callback_data=f"verificar_{payment_id}")]]
            )
            # Em vez de só texto, se existir uma imagem anterior, edita legenda; se não, substitui por texto
            msg_id = last_message_id.get(user_id)
            try:
                if msg_id:
                    await context.bot.edit_message_caption(
                        chat_id=user_id, message_id=msg_id, caption=texto, reply_markup=kb, parse_mode="Markdown"
                    )
                else:
                    await send_or_replace_text(context, user_id, texto)
            except Exception:
                await send_or_replace_text(context, user_id, texto)

# ------------------------------- Comandos -----------------------------------

# Cache simples para substituição de mensagens e controle de jobs
last_msg_ids: Dict[int, int] = {}
recurring_jobs: Dict[int, "Job"] = {}  # armazenamos o job de cobrança recorrente por usuário


async def _delete_previous_and_send_text(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = "Markdown"
):
    """Apaga a última mensagem enviada ao usuário (se existir) e envia uma nova."""
    old_id = last_msg_ids.get(chat_id)
    if old_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:
            pass
    msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
    last_msg_ids[chat_id] = msg.message_id
    return msg


async def _delete_previous_and_send_photo(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    photo_bytes: io.BytesIO,
    caption: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = "Markdown"
):
    """Apaga a última mensagem e envia uma foto (banner + QR)."""
    old_id = last_msg_ids.get(chat_id)
    if old_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:
            pass
    msg = await context.bot.send_photo(chat_id=chat_id, photo=photo_bytes, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)
    last_msg_ids[chat_id] = msg.message_id
    return msg


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra status do cadastro e próxima cobrança."""
    cliente = clientes_manager.get_cliente(update.effective_user.id)
    if not cliente:
        await _delete_previous_and_send_text(context, update.effective_user.id, "Você ainda não está configurado. Use /start.")
        return

    hoje = datetime.now()
    prox_mes = (hoje.month % 12) + 1
    ano = hoje.year + (1 if prox_mes == 1 else 0)
    proxima_data = datetime(ano, prox_mes if hoje.day > cliente["dia_pagamento"] else hoje.month, cliente["dia_pagamento"])
    txt = (
        f"📊 *Seu cadastro*\n"
        f"- Dia da cobrança: *{cliente['dia_pagamento']}*\n"
        f"- Valor: *R$ {cliente['valor']:.2f}*\n"
        f"- Próxima execução: *{proxima_data.strftime('%d/%m/%Y')}*\n"
    )
    await _delete_previous_and_send_text(context, update.effective_user.id, txt)


async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gera cobrança manual (substitui mensagens)."""
    user_id = update.effective_user.id
    cliente = clientes_manager.get_cliente(user_id)
    if not cliente:
        await _delete_previous_and_send_text(context, user_id, "Use /start para configurar primeiro.")
        return

    # Mensagem de loading substituindo anterior
    await _delete_previous_and_send_text(context, user_id, "⏳ Gerando cobrança...")

    # Chama a função existente que já gera a cobrança (texto + imagem).
    # Observação: se quiser que APENAS UMA mensagem apareça, adapte sua `gerar_cobranca`
    # para retornar o banner pronto e use `_delete_previous_and_send_photo` para enviar.
    await gerar_cobranca(user_id, cliente["username"], cliente["valor"], context, cliente.get("cpf_cnpj"))


# ---------- CALLBACKS (botão "Já paguei") ----------

async def verificar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica pagamento quando o usuário clica em 'Já paguei'."""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.from_user.id

    if data.startswith("verificar_"):
        payment_id = data.replace("verificar_", "")

        # feedback imediato (substitui)
        try:
            # Deleta a mensagem do botão e manda o "verificando..."
            await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
        except Exception:
            pass

        await _delete_previous_and_send_text(context, chat_id, "⏳ Verificando pagamento na rede...")

        # Chama a função de verificação
        resultado = await verificar_pagamento(payment_id)

        if resultado.get("success") and resultado.get("paid"):
            # ✅ Pago: cancela cobrança recorrente (se existir) e confirma
            job = recurring_jobs.pop(chat_id, None)
            if job:
                try:
                    job.schedule_removal()
                except Exception:
                    pass

            await _delete_previous_and_send_text(
                context,
                chat_id,
                "✅ *Pagamento confirmado!*\n\nObrigado! Sua próxima cobrança virá no mês seguinte.",
            )
        else:
            # ❌ Não pago: mensagem que você pediu, e mantemos o job recorrente ativo
            await _delete_previous_and_send_text(
                context,
                chat_id,
                "❌ *Pagamento não localizado.*\n\n"
                "Se isso for um erro, contate o suporte com seu comprovante.\n"
                "Caso não tenha efetuado o pagamento, realize-o e toque novamente em *Já paguei*."
            )


# ---------- JOBS (cobrança a cada 2h no dia da cobrança) ----------

async def _job_cobrar_2h(context: ContextTypes.DEFAULT_TYPE):
    """Job que roda a cada 2h e gera a cobrança se hoje for o dia do cliente."""
    now = datetime.now()
    user_id: int = context.job.data["user_id"]
    cliente = clientes_manager.get_cliente(user_id)
    if not cliente:
        # nada para fazer, remove job
        job = recurring_jobs.pop(user_id, None)
        if job:
            try:
                job.schedule_removal()
            except Exception:
                pass
        return

    # Se não é o dia combinado, encerra o job (para não ficar rodando fora do dia)
    if now.day != int(cliente["dia_pagamento"]):
        job = recurring_jobs.pop(user_id, None)
        if job:
            try:
                job.schedule_removal()
            except Exception:
                pass
        return

    # Envia a cobrança (substituindo mensagem anterior com um aviso rápido antes)
    await _delete_previous_and_send_text(context, user_id, "⏳ Gerando cobrança...")
    await gerar_cobranca(user_id, cliente["username"], cliente["valor"], context, cliente.get("cpf_cnpj"))


async def preparar_cobrancas_do_dia(context: ContextTypes.DEFAULT_TYPE):
    """
    Roda diariamente e inicia (se ainda não existir) um job a cada 2h
    para todos os clientes cujo dia_pagamento é hoje.
    """
    hoje = datetime.now().day
    clientes_hoje = clientes_manager.get_clientes_do_dia(hoje)

    for uid, dados in clientes_hoje:
        uid_int = int(uid)
        if uid_int in recurring_jobs:
            continue  # já existe job recorrente hoje

        # cria job repetindo a cada 2h, primeira execução imediata
        job = context.job_queue.run_repeating(
            _job_cobrar_2h,
            interval=2 * 60 * 60,  # 2h
            first=0,               # dispara agora
            data={"user_id": uid_int},
            name=f"cobranca_{uid_int}"
        )
        recurring_jobs[uid_int] = job


# ---------- MAIN (registrando handlers e jobs) ----------

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN não configurado!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("pagar", pagar))

    # Texto genérico
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Callback de botão "Já paguei"
    app.add_handler(CallbackQueryHandler(verificar_callback))

    # Job diário para preparar as cobranças do dia (8h da manhã)
    if app.job_queue:
        app.job_queue.run_daily(preparar_cobrancas_do_dia, time=time(hour=8, minute=0, second=0))

    logger.info("🤖 Bot iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
