#!/usr/bin/env python3
"""
Bot Telegram para cobranças automáticas via Depix
Sistema limpo e profissional para clientes
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

print("=" * 50)
print("🚀 INICIANDO BOT...")
print("=" * 50)

# Configurações
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS', 'lq1qqw3nx0darshqqzl8t95j0vj3xxuwmp4a4fyz799plu7m8d4ztr2jugftryer3khq0jmskgppe6ughwyevgwmuvq8de75sgyy2')
ATLAS_API_KEY = 'atlas_ceaf6237e499f94dfe87ef62b19e25b360293369cbacfdf99760ee255761b5f5'
ATLAS_API_CREATE = 'https://api.atlasdao.info/api/v1/external/pix/create'
ATLAS_API_STATUS = 'https://api.atlasdao.info/api/v1/external/pix/status'

print(f"✅ Token Telegram: {'OK' if TELEGRAM_TOKEN else '❌ FALTANDO'}")
print(f"✅ Wallet Address: {WALLET_ADDRESS[:20]}..." if len(WALLET_ADDRESS) > 20 else f"⚠️ Wallet: {WALLET_ADDRESS}")
print(f"✅ API Key: OK")
print("-" * 50)

# Arquivo para salvar dados
DATA_FILE = 'usuarios.json'

# Estados dos usuários
user_states: Dict[int, str] = {}


# ----------------- Funções auxiliares -----------------

def limpar_cpf_cnpj(documento: str) -> str:
    """Remove caracteres não numéricos"""
    return ''.join(filter(str.isdigit, documento or ''))


def formatar_cpf_cnpj(documento: str) -> str:
    """Formata CPF/CNPJ para exibição"""
    doc = limpar_cpf_cnpj(documento)
    if len(doc) == 11:
        return f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}"
    if len(doc) == 14:
        return f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}"
    return doc


# ----------------- Classe de Gerenciamento -----------------

class ClienteManager:
    """Gerencia dados dos clientes"""
    
    def __init__(self):
        self.clientes: Dict = self.load_data()
    
    def load_data(self) -> Dict:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r') as f:
                    return json.load(f)
            return {}
        except:
            return {}
    
    def save_data(self):
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump(self.clientes, f, indent=2)
        except Exception as e:
            logger.error(f"Erro ao salvar: {e}")
    
    def add_cliente(self, user_id: int, username: str, dia: int, valor: float, cpf_cnpj: str):
        documento = limpar_cpf_cnpj(cpf_cnpj)
        self.clientes[str(user_id)] = {
            'username': username,
            'dia_pagamento': dia,
            'valor': valor,
            'ativo': True,
            'ultima_cobranca': None,
            'ultimo_payment_id': None,
            'ultimo_merchant_id': None,
            'cpf_cnpj': documento
        }
        self.save_data()

    def get_cliente(self, user_id: int):
        cliente = self.clientes.get(str(user_id))
        if cliente and 'cpf_cnpj' not in cliente:
            cliente['cpf_cnpj'] = None
        return cliente
    
    def update_payment_id(self, user_id: int, payment_id: str, merchant_id: str):
        """Salva IDs do pagamento"""
        if str(user_id) in self.clientes:
            self.clientes[str(user_id)]['ultimo_payment_id'] = payment_id
            self.clientes[str(user_id)]['ultimo_merchant_id'] = merchant_id
            self.save_data()
    
    def get_clientes_do_dia(self, dia: int) -> list:
        return [
            (user_id, dados) 
            for user_id, dados in self.clientes.items()
            if dados['dia_pagamento'] == dia and dados['ativo']
        ]


# ----------------- Funções principais -----------------

def formatar_valor(valor: float) -> float:
    return round(float(valor), 2)


async def verificar_pagamento(payment_id: str) -> Dict:
    """Verifica status do pagamento via API"""
    try:
        url = f"{ATLAS_API_STATUS}/{payment_id}"
        headers = {'X-API-Key': ATLAS_API_KEY}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return {'success': True, 'paid': data.get('status') == 'PAID', 'data': data}
        return {'success': False, 'error': 'Erro ao verificar'}
    except Exception as e:
        logger.error(f"Erro verificação: {e}")
        return {'success': False, 'error': str(e)}


async def gerar_cobranca(user_id: int, username: str, valor: float, context: ContextTypes.DEFAULT_TYPE, tax_number: Optional[str] = None) -> Dict:
    """Gera cobrança via API Depix"""
    valor_formatado = formatar_valor(valor)
    if not tax_number:
        cliente = clientes_manager.get_cliente(user_id)
        if cliente:
            tax_number = cliente.get('cpf_cnpj')
    tax_number = limpar_cpf_cnpj(tax_number or '')

    if not tax_number:
        await context.bot.send_message(chat_id=user_id, text="❌ CPF/CNPJ ausente. Use /start novamente.")
        return {'success': False, 'error': 'cpf_cnpj ausente'}

    payload = {
        "amount": valor_formatado,
        "description": "Assinatura Mensal OMTB",
        "taxNumber": tax_number,
        "walletAddress": WALLET_ADDRESS
    }
    
    headers = {'X-API-Key': ATLAS_API_KEY, 'Content-Type': 'application/json'}
    try:
        response = requests.post(ATLAS_API_CREATE, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            payment_id = data.get('id')
            merchant_id = data.get('merchantOrderId')
            qr_code_string = data.get('qrCode')
            qr_code_base64 = data.get('qrCodeImage')
            
            clientes_manager.update_payment_id(user_id, payment_id, merchant_id)
            
            keyboard = [
                [InlineKeyboardButton("✅ Realizei o pagamento", callback_data=f"verificar_{payment_id}")],
                [InlineKeyboardButton("🔙 Voltar pra opção anterior", callback_data='voltar_anterior')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            mensagem = (
                f"📃 *Informações de pagamento*\n"
                f"Assinatura Mensal OMTB\n\n"
                f"💰 Valor: R$ {valor_formatado:.2f}\n\n"
                f"🔑 *Chave PIX (Copia e Cola)*\n"
                f"```\n{qr_code_string}\n```\n\n"
                f"⚠️ Após o pagamento, toque abaixo.\n"
                f"⏰ Expira em 30 minutos.\n\n"
                f"_Cobrança Depix não reembolsável_"
            )

            msg = await context.bot.send_message(chat_id=user_id, text=mensagem, parse_mode='Markdown', reply_markup=reply_markup)

            if qr_code_base64:
                try:
                    if ',' in qr_code_base64:
                        qr_code_base64 = qr_code_base64.split(',')[1]
                    image_data = base64.b64decode(qr_code_base64)
                    image = Image.open(io.BytesIO(image_data))
                    buffer = io.BytesIO()
                    image.save(buffer, format='PNG')
                    buffer.seek(0)
                    await context.bot.send_photo(chat_id=user_id, photo=buffer, caption="📱 Escaneie o QR Code acima ou use a chave PIX")
                except Exception as e:
                    logger.error(f"Erro ao processar QR: {e}")

            if context.job_queue:
                context.job_queue.run_once(expirar_cobranca, when=1800, data={'chat_id': user_id, 'message_id': msg.message_id, 'payment_id': payment_id})

            return {'success': True, 'payment_id': payment_id}
        else:
            logger.error(f"Erro API: {response.status_code} - {response.text}")
            await context.bot.send_message(chat_id=user_id, text="❌ Erro ao gerar cobrança.")
            return {'success': False}
    except Exception as e:
        logger.error(f"Erro: {e}")
        await context.bot.send_message(chat_id=user_id, text="❌ Erro ao processar pagamento.")
        return {'success': False, 'error': str(e)}


clientes_manager = ClienteManager()


# ----------------- Fluxo de Conversa -----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_states[user.id] = 'day'
    context.user_data.clear()
    await update.message.reply_text(f"Bem-vindo, *{user.first_name}*!\n\n📅 Qual dia do mês você deseja pagar?", parse_mode='Markdown')


async def receber_dia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        dia = int(update.message.text.strip())
        if dia < 1 or dia > 31:
            await update.message.reply_text("Digite um dia entre 1 e 31.")
            return
        context.user_data['dia'] = dia
        user_states[update.effective_user.id] = 'amount'
        await update.message.reply_text(
            "💵 Qual o valor (até 3000)?",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data='voltar_day')]])
        )
    except ValueError:
        await update.message.reply_text("Digite apenas números.")


async def receber_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valor = float(update.message.text.strip().replace(',', '.'))
        if valor <= 0 or valor > 3000:
            await update.message.reply_text("Valor inválido.")
            return
        context.user_data['valor'] = valor
        user_states[update.effective_user.id] = 'tax'
        await update.message.reply_text(
            "Informe o CPF ou CNPJ (apenas números).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data='voltar_amount')]])
        )
    except ValueError:
        await update.message.reply_text("Digite apenas números.")


async def receber_cpf_cnpj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    documento = limpar_cpf_cnpj(update.message.text)
    if len(documento) not in (11, 14):
        await update.message.reply_text("Documento inválido. Informe CPF (11) ou CNPJ (14).")
        return

    dia = context.user_data.get('dia')
    valor = context.user_data.get('valor')
    user = update.effective_user
    username = user.first_name or user.username or f"User{user.id}"
    clientes_manager.add_cliente(user.id, username, dia, valor, documento)

    user_states[user.id] = None
    context.user_data.clear()

    await update.message.reply_text(
        f"✅ Configurado!\nDia: *{dia}*\nValor: *R$ {valor:.2f}*\nDocumento: `{formatar_cpf_cnpj(documento)}`",
        parse_mode='Markdown'
    )

    if datetime.now().day == dia:
        await update.message.reply_text("⏰ Gerando sua cobrança...")
        await gerar_cobranca(user.id, username, valor, context, documento)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    estado = user_states.get(update.effective_user.id)
    if estado == 'day':
        await receber_dia(update, context)
    elif estado == 'amount':
        await receber_valor(update, context)
    elif estado == 'tax':
        await receber_cpf_cnpj(update, context)
    else:
        await update.message.reply_text("Use /start para iniciar.")


# ----------------- Callbacks -----------------

async def verificar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_text = query.data
    user_id = query.from_user.id

    # Voltar opções
    if data_text in {'voltar_day', 'voltar_amount', 'voltar_anterior'}:
        context.user_data.clear()
        user_states[user_id] = 'day'
        await context.bot.send_message(chat_id=user_id, text="📅 Qual dia do mês deseja pagar?", parse_mode='Markdown')
        return

    # Verificar pagamento
    if data_text.startswith('verificar_'):
        payment_id = data_text.replace('verificar_', '')
        await query.edit_message_text("⏳ Verificando pagamento...")
        resultado = await verificar_pagamento(payment_id)
        if resultado.get('success') and resultado.get('paid'):
            merchant_id = resultado['data'].get('merchantOrderId', 'N/A')
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ *Pagamento confirmado!*\n\nID: `{merchant_id}`",
                parse_mode='Markdown'
            )
        else:
            await context.bot.send_message(chat_id=user_id, text="⚠️ Pagamento ainda não confirmado.")


async def expirar_cobranca(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id, message_id, payment_id = job_data.values()
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

    keyboard = [
        [InlineKeyboardButton("💳 Fazer pagamento", callback_data=f"novopag_{payment_id}")],
        [InlineKeyboardButton("🔙 Voltar", callback_data='voltar_anterior')]
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ Cobrança expirada.\nToque abaixo para gerar nova cobrança.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ----------------- Comandos -----------------

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cliente = clientes_manager.get_cliente(update.effective_user.id)
    if not cliente:
        await update.message.reply_text("Você ainda não está configurado. Use /start.")
        return
    await update.message.reply_text(
        f"📊 Dia: {cliente['dia_pagamento']}\n"
        f"Valor: R$ {cliente['valor']:.2f}\n"
        f"Documento: {formatar_cpf_cnpj(cliente.get('cpf_cnpj', '')) or 'Não informado'}",
        parse_mode='Markdown'
    )


async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cliente = clientes_manager.get_cliente(update.effective_user.id)
    if not cliente:
        await update.message.reply_text("Use /start para configurar primeiro.")
        return
    if not cliente.get('cpf_cnpj'):
        await update.message.reply_text("Atualize seu CPF/CNPJ com /start antes de pagar.")
        return
    await update.message.reply_text("⏳ Gerando cobrança...")
    await gerar_cobranca(update.effective_user.id, cliente['username'], cliente['valor'], context, cliente['cpf_cnpj'])


# ----------------- Main -----------------

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN não configurado!")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('pagar', pagar))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(verificar_callback))

    if app.job_queue:
        app.job_queue.run_daily(verificar_cobrancas_diarias, time=time(hour=9, minute=0))
    logger.info("🤖 Bot iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


async def verificar_cobrancas_diarias(context: ContextTypes.DEFAULT_TYPE):
    hoje = datetime.now().day
    clientes_hoje = clientes_manager.get_clientes_do_dia(hoje)
    for user_id, dados in clientes_hoje:
        await gerar_cobranca(int(user_id), dados['username'], dados['valor'], context, dados.get('cpf_cnpj'))


if __name__ == '__main__':
    main()
