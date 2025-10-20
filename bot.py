#!/usr/bin/env python3
"""
Bot Telegram para cobranças automáticas via Atlas DAO
Fluxo: Configura dia + valor → Gera cobrança automática todo mês
"""

import os
import json
import random
import string
import logging
from datetime import datetime, time
from typing import Dict
import requests

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS', 'CONFIGURE_WALLET_LWK')
ATLAS_API_KEY = 'atlas_ceaf6237e499f94dfe87ef62b19e25b360293369cbacfdf99760ee255761b5f5'
ATLAS_API_URL = 'https://api.atlasdao.info/api/v1'

# Estados da conversa
WAITING_DAY, WAITING_AMOUNT = range(2)

# Arquivo para salvar dados dos usuários
DATA_FILE = 'usuarios.json'


class ClienteManager:
    """Gerencia dados dos clientes"""
    
    def __init__(self):
        self.clientes: Dict = self.load_data()
    
    def load_data(self) -> Dict:
        """Carrega dados do arquivo"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Erro ao carregar dados: {e}")
            return {}
    
    def save_data(self):
        """Salva dados no arquivo"""
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump(self.clientes, f, indent=2)
        except Exception as e:
            logger.error(f"Erro ao salvar dados: {e}")
    
    def add_cliente(self, user_id: int, username: str, dia: int, valor: float):
        """Adiciona ou atualiza cliente"""
        self.clientes[str(user_id)] = {
            'username': username,
            'dia_pagamento': dia,
            'valor': valor,
            'ativo': True,
            'ultima_cobranca': None
        }
        self.save_data()
    
    def get_cliente(self, user_id: int):
        """Retorna dados do cliente"""
        return self.clientes.get(str(user_id))
    
    def get_clientes_do_dia(self, dia: int) -> list:
        """Retorna clientes que pagam hoje"""
        return [
            (user_id, dados) 
            for user_id, dados in self.clientes.items()
            if dados['dia_pagamento'] == dia and dados['ativo']
        ]


def gerar_merchant_id() -> str:
    """Gera merchantOrderId aleatório de 10 caracteres"""
    caracteres = string.ascii_uppercase + string.digits
    return ''.join(random.choice(caracteres) for _ in range(10))


def formatar_valor(valor: float) -> float:
    """Garante formato X.XX"""
    return round(float(valor), 2)


async def gerar_cobranca(user_id: int, username: str, valor: float, context: ContextTypes.DEFAULT_TYPE) -> Dict:
    """Gera cobrança via API Atlas DAO"""
    
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
        logger.info(f"Gerando cobrança: {payload}")
        
        response = requests.post(
            ATLAS_API_URL,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"Cobrança gerada com sucesso: {merchant_id}")
            
            # Enviar cobrança para o usuário
            mensagem = (
                f"💰 *Nova Cobrança Gerada*\n\n"
                f"Valor: R$ {valor_formatado:.2f}\n"
                f"ID: `{merchant_id}`\n\n"
                f"Efetue o pagamento usando o QR Code abaixo:"
            )
            
            await context.bot.send_message(
                chat_id=user_id,
                text=mensagem,
                parse_mode='Markdown'
            )
            
            # Se a API retornar QR code, enviar
            if 'qrCode' in data:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=data['qrCode']
                )
            
            # Se retornar link de pagamento
            if 'paymentUrl' in data:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🔗 Link de pagamento: {data['paymentUrl']}"
                )
            
            return {'success': True, 'merchant_id': merchant_id, 'data': data}
        
        else:
            logger.error(f"Erro na API: {response.status_code} - {response.text}")
            return {'success': False, 'error': response.text}
    
    except Exception as e:
        logger.error(f"Erro ao gerar cobrança: {e}")
        return {'success': False, 'error': str(e)}


# Instância global do gerenciador
clientes_manager = ClienteManager()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Comando /start - Inicia configuração"""
    user = update.effective_user
    
    await update.message.reply_text(
        f"👋 Bem vindo *{user.first_name}*!\n\n"
        f"Vou configurar sua cobrança automática.\n\n"
        f"📅 Qual dia do mês você quer pagar? (1-31)",
        parse_mode='Markdown'
    )
    
    return WAITING_DAY


async def receber_dia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o dia do pagamento"""
    try:
        dia = int(update.message.text.strip())
        
        if dia < 1 or dia > 31:
            await update.message.reply_text(
                "❌ Por favor, digite um dia válido entre 1 e 31."
            )
            return WAITING_DAY
        
        # Salvar dia temporariamente
        context.user_data['dia'] = dia
        
        await update.message.reply_text(
            f"✅ Dia {dia} configurado!\n\n"
            f"💵 Qual o valor da cobrança?\n"
            f"(Digite apenas o número, até R$ 3000,00)\n"
            f"Exemplo: 150"
        )
        
        return WAITING_AMOUNT
    
    except ValueError:
        await update.message.reply_text(
            "❌ Por favor, digite apenas números.\n"
            "Exemplo: 15"
        )
        return WAITING_DAY


async def receber_valor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o valor do pagamento"""
    try:
        valor = float(update.message.text.strip().replace(',', '.'))
        
        if valor <= 0 or valor > 3000:
            await update.message.reply_text(
                "❌ Valor inválido! Digite um valor entre R$ 0,01 e R$ 3.000,00"
            )
            return WAITING_AMOUNT
        
        # Recuperar dados
        dia = context.user_data['dia']
        user = update.effective_user
        user_id = user.id
        username = user.first_name or user.username or f"User{user_id}"
        
        # Salvar cliente
        clientes_manager.add_cliente(user_id, username, dia, valor)
        
        await update.message.reply_text(
            f"✅ *Configuração Completa!*\n\n"
            f"📅 Dia do pagamento: {dia}\n"
            f"💰 Valor: R$ {valor:.2f}\n\n"
            f"Todo dia {dia} você receberá uma cobrança automática.\n\n"
            f"Use /status para ver sua configuração\n"
            f"Use /cancelar para cancelar cobranças",
            parse_mode='Markdown'
        )
        
        # Se hoje for o dia, gerar cobrança imediatamente
        hoje = datetime.now().day
        if hoje == dia:
            await update.message.reply_text(
                "⏰ Hoje é seu dia de pagamento!\n"
                "Gerando cobrança agora..."
            )
            await gerar_cobranca(user_id, username, valor, context)
        
        return ConversationHandler.END
    
    except ValueError:
        await update.message.reply_text(
            "❌ Por favor, digite apenas números.\n"
            "Exemplo: 150 ou 150.50"
        )
        return WAITING_AMOUNT


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra status da configuração"""
    user_id = update.effective_user.id
    cliente = clientes_manager.get_cliente(user_id)
    
    if not cliente:
        await update.message.reply_text(
            "❌ Você ainda não configurou cobranças.\n"
            "Use /start para começar!"
        )
        return
    
    await update.message.reply_text(
        f"📊 *Sua Configuração*\n\n"
        f"📅 Dia do pagamento: {cliente['dia_pagamento']}\n"
        f"💰 Valor: R$ {cliente['valor']:.2f}\n"
        f"✅ Status: {'Ativo' if cliente['ativo'] else 'Cancelado'}\n\n"
        f"Use /start para reconfigurar\n"
        f"Use /cancelar para desativar",
        parse_mode='Markdown'
    )


async def cancelar_cobrancas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela cobranças automáticas"""
    user_id = update.effective_user.id
    cliente = clientes_manager.get_cliente(user_id)
    
    if not cliente:
        await update.message.reply_text("❌ Você não tem cobranças configuradas.")
        return
    
    cliente['ativo'] = False
    clientes_manager.save_data()
    
    await update.message.reply_text(
        "🛑 Cobranças automáticas canceladas.\n\n"
        "Use /start para reativar."
    )


async def cancelar_conversa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela a conversa"""
    await update.message.reply_text(
        "❌ Configuração cancelada.\n"
        "Use /start para começar novamente."
    )
    return ConversationHandler.END


async def verificar_cobrancas_diarias(context: ContextTypes.DEFAULT_TYPE):
    """Job que roda diariamente para gerar cobranças"""
    hoje = datetime.now().day
    logger.info(f"🔍 Verificando cobranças para o dia {hoje}")
    
    clientes_hoje = clientes_manager.get_clientes_do_dia(hoje)
    
    if not clientes_hoje:
        logger.info("Nenhuma cobrança para hoje")
        return
    
    logger.info(f"Encontrados {len(clientes_hoje)} clientes para cobrar")
    
    for user_id, dados in clientes_hoje:
        try:
            resultado = await gerar_cobranca(
                int(user_id),
                dados['username'],
                dados['valor'],
                context
            )
            
            if resultado['success']:
                # Atualizar última cobrança
                clientes_manager.clientes[user_id]['ultima_cobranca'] = datetime.now().isoformat()
                clientes_manager.save_data()
                logger.info(f"✅ Cobrança gerada para {dados['username']}")
            else:
                logger.error(f"❌ Erro ao cobrar {dados['username']}: {resultado.get('error')}")
        
        except Exception as e:
            logger.error(f"Erro ao processar cliente {user_id}: {e}")


def main():
    """Função principal"""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN não configurado!")
    
    # Criar aplicação
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handler de conversa
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
    app.add_handler(CommandHandler('cancelar', cancelar_cobrancas))
    
    # Job diário às 9h da manhã
    app.job_queue.run_daily(
        verificar_cobrancas_diarias,
        time=time(hour=9, minute=0),
        name='cobrancas_diarias'
    )
    
    logger.info("🤖 Bot iniciado!")
    logger.info(f"💳 Wallet: {WALLET_ADDRESS}")
    logger.info("✅ Sistema de cobranças automáticas ativo")
    
    # Iniciar bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
