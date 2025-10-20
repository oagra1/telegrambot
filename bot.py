#!/usr/bin/env python3
"""
Bot Telegram para cobranças automáticas
Sistema limpo e profissional para clientes
"""

import os
import json
import random
import string
import logging
from datetime import datetime, time
from typing import Dict
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configurações
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS', 'CONFIGURE_WALLET')
ATLAS_API_KEY = 'atlas_ceaf6237e499f94dfe87ef62b19e25b360293369cbacfdf99760ee255761b5f5'
ATLAS_API_URL = 'https://api.atlasdao.info/api/v1'

# Estados da conversa
WAITING_DAY, WAITING_AMOUNT = range(2)

# Arquivo para salvar dados
DATA_FILE = 'usuarios.json'


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
    
    def add_cliente(self, user_id: int, username: str, dia: int, valor: float):
        self.clientes[str(user_id)] = {
            'username': username,
            'dia_pagamento': dia,
            'valor': valor,
            'ativo': True,
            'ultima_cobranca': None,
            'ultimo_merchant_id': None
        }
        self.save_data()
    
    def get_cliente(self, user_id: int):
        return self.clientes.get(str(user_id))
    
    def update_merchant_id(self, user_id: int, merchant_id: str):
        """Salva último merchant_id gerado"""
        if str(user_id) in self.clientes:
            self.clientes[str(user_id)]['ultimo_merchant_id'] = merchant_id
            self.save_data()
    
    def get_clientes_do_dia(self, dia: int) -> list:
        return [
            (user_id, dados) 
            for user_id, dados in self.clientes.items()
            if dados['dia_pagamento'] == dia and dados['ativo']
        ]


def gerar_merchant_id() -> str:
    """Gera ID aleatório de 10 caracteres"""
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10))


def formatar_valor(valor: float) -> float:
    """Formato X.XX"""
    return round(float(valor), 2)


async def verificar_pagamento(merchant_id: str) -> Dict:
    """Verifica status do pagamento via API"""
    try:
        url = f"{ATLAS_API_URL}/external/pix/status/{merchant_id}"
        headers = {'X-API-Key': ATLAS_API_KEY}
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Assumindo que API retorna: {"status": "paid"} ou similar
            return {
                'success': True,
                'paid': data.get('status') == 'paid',
                'data': data
            }
        else:
            return {'success': False, 'error': 'Erro ao verificar'}
    
    except Exception as e:
        logger.error(f"Erro verificação: {e}")
        return {'success': False, 'error': str(e)}


async def gerar_cobranca(user_id: int, username: str, valor: float, context: ContextTypes.DEFAULT_TYPE) -> Dict:
    """Gera cobrança via API"""
    
    merchant_id = gerar_merchant_id()
    valor_formatado = formatar_valor(valor)
    
    payload = {
        "amount": valor_formatado,
        "description": f"Pagamento por {username}",
        "walletAddress": WALLET_ADDRESS,
        "merchantOrderId": merchant_id
    }
    
    headers = {
        'X-API-Key': ATLAS_API_KEY,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(ATLAS_API_URL, json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            # Salvar merchant_id
            clientes_manager.update_merchant_id(user_id, merchant_id)
            
            # Botão para verificar pagamento
            keyboard = [
                [InlineKeyboardButton("✅ Realizei o pagamento", callback_data=f"verificar_{merchant_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Mensagem limpa
            mensagem = (
                f"💰 *Cobrança Gerada*\n\n"
                f"Valor: R$ {valor_formatado:.2f}\n"
                f"ID: `{merchant_id}`\n\n"
                f"Pague usando o QR Code abaixo.\n"
                f"Após pagar, clique no botão para confirmar."
            )
            
            await context.bot.send_message(
                chat_id=user_id,
                text=mensagem,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            
            # QR Code se disponível
            if 'qrCode' in data:
                await context.bot.send_photo(chat_id=user_id, photo=data['qrCode'])
            
            if 'paymentUrl' in data:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🔗 {data['paymentUrl']}"
                )
            
            return {'success': True, 'merchant_id': merchant_id}
        
        else:
            logger.error(f"Erro API: {response.text}")
            return {'success': False, 'error': response.text}
    
    except Exception as e:
        logger.error(f"Erro: {e}")
        return {'success': False, 'error': str(e)}


# Gerenciador global
clientes_manager = ClienteManager()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Comando /start"""
    user = update.effective_user
    
    await update.message.reply_text(
        f"Bem vindo, *{user.first_name}*!\n\n"
        f"📅 Qual dia do mês você deseja pagar?",
        parse_mode='Markdown'
    )
    
    return WAITING_DAY


async def receber_dia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe dia"""
    try:
        dia = int(update.message.text.strip())
        
        if dia < 1 or dia > 31:
            await update.message.reply_text("Por favor, digite um dia entre 1 e 31.")
            return WAITING_DAY
        
        context.user_data['dia'] = dia
        
        await update.message.reply_text(
            f"Perfeito!\n\n"
            f"💵 Qual o valor?\n"
            f"(Digite apenas o número, até 3000)"
        )
        
        return WAITING_AMOUNT
    
    except ValueError:
        await update.message.reply_text("Por favor, digite apenas números.")
        return WAITING_DAY


async def receber_valor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe valor"""
    try:
        valor = float(update.message.text.strip().replace(',', '.'))
        
        if valor <= 0 or valor > 3000:
            await update.message.reply_text("Valor inválido. Digite entre 0.01 e 3000")
            return WAITING_AMOUNT
        
        dia = context.user_data['dia']
        user = update.effective_user
        username = user.first_name or user.username or f"User{user.id}"
        
        # Salvar
        clientes_manager.add_cliente(user.id, username, dia, valor)
        
        await update.message.reply_text(
            f"✅ *Configurado com sucesso!*\n\n"
            f"Todo dia *{dia}* você receberá uma cobrança de *R$ {valor:.2f}*",
            parse_mode='Markdown'
        )
        
        # Se hoje é o dia, cobrar agora
        if datetime.now().day == dia:
            await update.message.reply_text("⏰ Gerando sua cobrança...")
            await gerar_cobranca(user.id, username, valor, context)
        
        return ConversationHandler.END
    
    except ValueError:
        await update.message.reply_text("Por favor, digite apenas números.")
        return WAITING_AMOUNT


async def verificar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback do botão 'Realizei o pagamento'"""
    query = update.callback_query
    await query.answer()
    
    # Extrair merchant_id
    merchant_id = query.data.replace('verificar_', '')
    
    await query.edit_message_text("⏳ Verificando pagamento...")
    
    # Verificar na API
    resultado = await verificar_pagamento(merchant_id)
    
    if resultado.get('success') and resultado.get('paid'):
        # PAGAMENTO CONFIRMADO
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="✅ *Pagamento confirmado!*\n\nObrigado! 🎉",
            parse_mode='Markdown'
        )
    else:
        # NÃO CONFIRMADO
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=(
                "⚠️ Pagamento ainda não confirmado.\n\n"
                "Se você já pagou, envie o comprovante e "
                "entre em contato com o suporte."
            )
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ver configuração"""
    cliente = clientes_manager.get_cliente(update.effective_user.id)
    
    if not cliente:
        await update.message.reply_text("Você ainda não está configurado.\nUse /start")
        return
    
    await update.message.reply_text(
        f"📊 *Sua configuração*\n\n"
        f"Dia: {cliente['dia_pagamento']}\n"
        f"Valor: R$ {cliente['valor']:.2f}",
        parse_mode='Markdown'
    )


async def cancelar_conversa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela"""
    await update.message.reply_text("Configuração cancelada.\nUse /start novamente.")
    return ConversationHandler.END


async def verificar_cobrancas_diarias(context: ContextTypes.DEFAULT_TYPE):
    """Job diário - 9h"""
    hoje = datetime.now().day
    logger.info(f"Verificando cobranças dia {hoje}")
    
    clientes_hoje = clientes_manager.get_clientes_do_dia(hoje)
    
    for user_id, dados in clientes_hoje:
        try:
            await gerar_cobranca(int(user_id), dados['username'], dados['valor'], context)
            logger.info(f"Cobrança gerada: {dados['username']}")
        except Exception as e:
            logger.error(f"Erro: {e}")


def main():
    """Iniciar bot"""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN não configurado!")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Conversa
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            WAITING_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_dia)],
            WAITING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_valor)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar_conversa)],
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CallbackQueryHandler(verificar_callback, pattern='^verificar_'))
    
    # Job diário 9h
    app.job_queue.run_daily(
        verificar_cobrancas_diarias,
        time=time(hour=9, minute=0)
    )
    
    logger.info("🤖 Bot iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
