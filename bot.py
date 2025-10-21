#!/usr/bin/env python3
"""
Bot Telegram para cobranças automáticas via Depix (Atlas DAO)
Sistema limpo e profissional, com logs de depuração e tratamento de erros aprimorado.
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
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        CallbackQueryHandler,
        filters,
        ContextTypes
    )
except ImportError as e:
    print(f"ERRO: python-telegram-bot não instalado! {e}")
    raise

try:
    from PIL import Image
except ImportError:
    print("AVISO: Pillow não instalado - QR Codes não funcionarão")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

print("=" * 50)
print("🚀 INICIANDO BOT...")
print("=" * 50)

# Configurações
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WALLET_ADDRESS = os.getenv(
    "WALLET_ADDRESS",
    "lq1qqw3nx0darshqqzl8t95j0vj3xxuwmp4a4fyz799plu7m8d4ztr2jugftryer3khq0jmskgppe6ughwyevgwmuvq8de75sgyy2"
)
ATLAS_API_KEY = "atlas_ceaf6237e499f94dfe87ef62b19e25b360293369cbacfdf99760ee255761b5f5"
ATLAS_API_CREATE = "https://api.atlasdao.info/api/v1/external/pix/create"
ATLAS_API_STATUS = "https://api.atlasdao.info/api/v1/external/pix/status"

print(f"✅ Token Telegram: {'OK' if TELEGRAM_TOKEN else '❌ FALTANDO'}")
print(f"✅ Wallet Address: {WALLET_ADDRESS[:20]}...")
print("✅ API Key: OK")
print("-" * 50)

DATA_FILE = "usuarios.json"
user_states: Dict[int, str] = {}


# ---------- FUNÇÕES AUXILIARES ----------

def limpar_cpf_cnpj(documento: str) -> str:
    return "".join(filter(str.isdigit, documento or ""))


def formatar_cpf_cnpj(documento: str) -> str:
    doc = limpar_cpf_cnpj(documento)
    if len(doc) == 11:
        return f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}"
    if len(doc) == 14:
        return f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}"
    return doc


# ---------- GERENCIADOR DE CLIENTES ----------

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

    def add_cliente(self, user_id: int, username: str, dia: int, valor: float, cpf_cnpj: str):
        doc = limpar_cpf_cnpj(cpf_cnpj)
        self.clientes[str(user_id)] = {
            "username": username,
            "dia_pagamento": dia,
            "valor": valor,
            "cpf_cnpj": doc,
            "ativo": True,
        }
        self.save_data()

    def get_cliente(self, user_id: int):
        return self.clientes.get(str(user_id))

    def get_clientes_do_dia(self, dia: int):
        return [
            (uid, c) for uid, c in self.clientes.items()
            if c["dia_pagamento"] == dia and c.get("ativo")
        ]


clientes_manager = ClienteManager()


# ---------- API PIX / DEPAGOS ----------

async def gerar_cobranca(
    user_id: int, username: str, valor: float, context: ContextTypes.DEFAULT_TYPE, tax_number: Optional[str] = None
):
    """Gera cobrança via API Atlas DAO / Depix"""

    valor_formatado = round(float(valor), 2)
    if not tax_number:
        cliente = clientes_manager.get_cliente(user_id)
        if cliente:
            tax_number = cliente.get("cpf_cnpj")

    tax_number = limpar_cpf_cnpj(tax_number or "")
    if not tax_number:
        await context.bot.send_message(chat_id=user_id, text="❌ CPF/CNPJ ausente. Use /start novamente.")
        return

    payload = {
        "amount": valor_formatado,
        "description": "Assinatura Mensal OMTB",
        "taxNumber": tax_number,
        "walletAddress": WALLET_ADDRESS,
    }

    headers = {"X-API-Key": ATLAS_API_KEY, "Content-Type": "application/json"}

    # --- LOG DEBUG ---
    logger.info(f"➡️ Enviando requisição para {ATLAS_API_CREATE}")
    logger.info(f"PAYLOAD: {json.dumps(payload, ensure_ascii=False)}")
    logger.info(f"HEADERS: {headers}")

    try:
        response = requests.post(ATLAS_API_CREATE, json=payload, headers=headers, timeout=30)

        # Debug completo da resposta
        logger.info(f"⬅️ STATUS: {response.status_code}")
        logger.info(f"⬅️ BODY: {response.text}")

        if not response.ok:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Erro ao gerar cobrança.\n\n📡 Código: {response.status_code}\n💬 {response.text}",
            )
            return

        data = response.json()
        payment_id = data.get("id")
        qr_string = data.get("qrCode")
        qr_image = data.get("qrCodeImage")

        mensagem = (
            f"📃 *Informações de pagamento*\n"
            f"💰 Valor: R$ {valor_formatado:.2f}\n\n"
            f"🔑 *Chave PIX (Copia e Cola)*\n"
            f"```\n{qr_string}\n```\n\n"
            f"⏰ Expira em 30 minutos.\n"
            f"_Cobrança Depix não reembolsável_"
        )

        # Envia texto com botão
        botoes = [[InlineKeyboardButton("✅ Já paguei", callback_data=f"verificar_{payment_id}")]]
        msg = await context.bot.send_message(
            chat_id=user_id, text=mensagem, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(botoes)
        )

        # Envia imagem se existir
        if qr_image:
            try:
                if "," in qr_image:
                    qr_image = qr_image.split(",")[1]
                image_data = base64.b64decode(qr_image)
                image = Image.open(io.BytesIO(image_data))
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                buf.seek(0)
                await context.bot.send_photo(chat_id=user_id, photo=buf, caption="📱 Escaneie o QR Code acima.")
            except Exception as e:
                logger.error(f"Erro ao decodificar imagem base64: {e}")

        return data

    except Exception as e:
        logger.exception("❌ Erro geral na geração da cobrança")
        await context.bot.send_message(chat_id=user_id, text=f"❌ Erro interno: {str(e)}")


# ---------- FLUXO DE CONVERSA ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_states[user.id] = "day"
    context.user_data.clear()
    await update.message.reply_text(
        f"Bem-vindo, *{user.first_name}*!\n\n📅 Qual dia do mês você deseja pagar?", parse_mode="Markdown"
    )


async def receber_dia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        dia = int(update.message.text.strip())
        if not 1 <= dia <= 31:
            await update.message.reply_text("Digite um dia entre 1 e 31.")
            return
        context.user_data["dia"] = dia
        user_states[update.effective_user.id] = "amount"
        await update.message.reply_text("💵 Qual o valor (até 3000)?")
    except ValueError:
        await update.message.reply_text("Digite apenas números.")


async def receber_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valor = float(update.message.text.replace(",", "."))
        if valor <= 0 or valor > 3000:
            await update.message.reply_text("Valor inválido.")
            return
        context.user_data["valor"] = valor
        user_states[update.effective_user.id] = "tax"
        await update.message.reply_text("Informe o CPF ou CNPJ (somente números).")
    except ValueError:
        await update.message.reply_text("Digite apenas números.")


async def receber_cpf_cnpj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = limpar_cpf_cnpj(update.message.text)
    if len(doc) not in (11, 14):
        await update.message.reply_text("Documento inválido. Informe CPF (11) ou CNPJ (14).")
        return

    dia = context.user_data.get("dia")
    valor = context.user_data.get("valor")
    user = update.effective_user
    username = user.first_name or user.username or f"User{user.id}"

    clientes_manager.add_cliente(user.id, username, dia, valor, doc)
    await update.message.reply_text(
        f"✅ Configurado!\nDia: *{dia}*\nValor: *R$ {valor:.2f}*\nDocumento: `{formatar_cpf_cnpj(doc)}`",
        parse_mode="Markdown",
    )

    if datetime.now().day == dia:
        await update.message.reply_text("⏰ Gerando sua cobrança...")
        await gerar_cobranca(user.id, username, valor, context, doc)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    estado = user_states.get(update.effective_user.id)
    if estado == "day":
        await receber_dia(update, context)
    elif estado == "amount":
        await receber_valor(update, context)
    elif estado == "tax":
        await receber_cpf_cnpj(update, context)
    else:
        await update.message.reply_text("Use /start para iniciar.")


# ---------- COMANDOS ----------

async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cliente = clientes_manager.get_cliente(update.effective_user.id)
    if not cliente:
        await update.message.reply_text("Use /start para configurar primeiro.")
        return
    await update.message.reply_text("⏳ Gerando cobrança...")
    await gerar_cobranca(update.effective_user.id, cliente["username"], cliente["valor"], context, cliente["cpf_cnpj"])


# ---------- MAIN ----------

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN não configurado!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pagar", pagar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 Bot iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
