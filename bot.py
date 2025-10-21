#!/usr/bin/env python3
"""
Bot Telegram para cobranças automáticas via Depix
Sistema limpo e profissional para clientes
"""

import os
import json
import logging
from datetime import datetime, time
from typing import Dict
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
        ContextTypes,
        ConversationHandler
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
            'ultimo_payment_id': None,
            'ultimo_merchant_id': None
        }
        self.save_data()
    
    def get_cliente(self, user_id: int):
        return self.clientes.get(str(user_id))
    
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


def formatar_valor(valor: float) -> float:
    """Formato X.XX"""
    return round(float(valor), 2)


async def verificar_pagamento(payment_id: str) -> Dict:
    """Verifica status do pagamento via API usando o ID retornado"""
    try:
        url = f"{ATLAS_API_STATUS}/{payment_id}"
        headers = {'X-API-Key': ATLAS_API_KEY}
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Status pode ser: PENDING, PAID, EXPIRED, etc
            return {
                'success': True,
                'paid': data.get('status') == 'PAID',
                'data': data
            }
        else:
            return {'success': False, 'error': 'Erro ao verificar'}
    
    except Exception as e:
        logger.error(f"Erro verificação: {e}")
        return {'success': False, 'error': str(e)}


async def gerar_cobranca(user_id: int, username: str, valor: float, context: ContextTypes.DEFAULT_TYPE) -> Dict:
    """Gera cobrança via API Depix"""
    
    valor_formatado = formatar_valor(valor)
    
    payload = {
        "amount": valor_formatado,
        "description": "Assinatura Mensal OMTB",
        "taxNumber": "",
        "walletAddress": WALLET_ADDRESS
    }
    
    headers = {
        'X-API-Key': ATLAS_API_KEY,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(ATLAS_API_CREATE, json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            # Extrair dados da resposta
            payment_id = data.get('id')  # UUID para verificação
            merchant_id = data.get('merchantOrderId')  # ID da compra
            qr_code_string = data.get('qrCode')  # String copia e cola
            qr_code_base64 = data.get('qrCodeImage')  # Base64 da imagem
            
            # Salvar IDs
            clientes_manager.update_payment_id(user_id, payment_id, merchant_id)
            
            # Botão para verificar pagamento
            keyboard = [
                [InlineKeyboardButton("✅ Realizei o pagamento", callback_data=f"verificar_{payment_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Mensagem organizada
            mensagem = (
                f"📃 *Informações de pagamento*\n"
                f"Assinatura Mensal OMTB\n\n"
                f"💰 Valor: R$ {valor_formatado:.2f}\n\n"
                f"🔑 *Chave PIX (Copia e Cola)*\n"
                f"```\n{qr_code_string}\n```\n\n"
                f"⚠️ *Atenção!*\n"
                f"Após o pagamento, toque no botão abaixo.\n\n"
                f"⏰ Esta cobrança expira em 30 minutos.\n\n"
                f"_Essa é uma compra de Depix não reembolsável_"
            )
            
            # Enviar mensagem com botão
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=mensagem,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            
            # Converter base64 para imagem e enviar
            if qr_code_base64:
                try:
                    # Remover prefixo data:image/png;base64, se existir
                    if ',' in qr_code_base64:
                        qr_code_base64 = qr_code_base64.split(',')[1]
                    
                    # Decodificar base64
                    image_data = base64.b64decode(qr_code_base64)
                    image = Image.open(io.BytesIO(image_data))
                    
                    # Salvar em buffer
                    buffer = io.BytesIO()
                    image.save(buffer, format='PNG')
                    buffer.seek(0)
                    
                    # Enviar imagem
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=buffer,
                        caption="📱 Escaneie o QR Code acima ou use a chave PIX"
                    )
                except Exception as e:
                    logger.error(f"Erro ao processar imagem QR Code: {e}")
            
            # Agendar exclusão após 30 minutos
            if context.job_queue:
                context.job_queue.run_once(
                    expirar_cobranca,
                    when=1800,  # 30 minutos
                    data={'chat_id': user_id, 'message_id': msg.message_id, 'payment_id': payment_id}
                )
            
            return {'success': True, 'payment_id': payment_id, 'merchant_id': merchant_id}
        
        else:
            logger.error(f"Erro API: {response.status_code} - {response.text}")
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Erro ao gerar cobrança. Tente novamente em instantes."
            )
            return {'success': False, 'error': response.text}
    
    except Exception as e:
        logger.error(f"Erro: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Erro ao processar pagamento. Contate o suporte."
        )
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
    
    data_text = query.data
    
    if data_text.startswith('verificar_'):
        payment_id = data_text.replace('verificar_', '')
        
        await query.edit_message_text("⏳ Verificando pagamento...")
        
        # Verificar na API
        resultado = await verificar_pagamento(payment_id)
        
        if resultado.get('success') and resultado.get('paid'):
            # PAGAMENTO CONFIRMADO
            # Pegar merchantOrderId do resultado
            merchant_id = resultado.get('data', {}).get('merchantOrderId', 'N/A')
            
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=(
                    f"✅ *Pagamento confirmado!*\n\n"
                    f"Sua compra foi confirmada.\n"
                    f"ID da compra: `{merchant_id}`\n\n"
                    f"Obrigado! 🎉"
                ),
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
    
    elif data_text.startswith('novopag_'):
        # Botão "Fazer pagamento" após expiração
        payment_id_antigo = data_text.replace('novopag_', '')
        user_id = query.from_user.id
        
        # Pegar dados do cliente
        cliente = clientes_manager.get_cliente(user_id)
        if cliente:
            await query.edit_message_text("⏳ Gerando nova cobrança...")
            await gerar_cobranca(user_id, cliente['username'], cliente['valor'], context)
        else:
            await query.edit_message_text("❌ Erro. Use /start para configurar novamente.")


async def expirar_cobranca(context: ContextTypes.DEFAULT_TYPE):
    """Função chamada após 30 min para expirar cobrança"""
    job_data = context.job.data
    chat_id = job_data['chat_id']
    message_id = job_data['message_id']
    payment_id = job_data['payment_id']
    
    try:
        # Deletar mensagem antiga
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Não foi possível deletar mensagem: {e}")
    
    # Enviar nova mensagem com botão
    keyboard = [
        [InlineKeyboardButton("💳 Fazer pagamento", callback_data=f"novopag_{payment_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⏰ *Cobrança expirada*\n\n"
            "O prazo de 30 minutos expirou.\n"
            "Toque no botão abaixo para gerar um novo pagamento."
        ),
        parse_mode='Markdown',
        reply_markup=reply_markup
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
    app.add_handler(CallbackQueryHandler(verificar_callback))
    
    # Job diário 9h
    try:
        if app.job_queue:
            app.job_queue.run_daily(
                verificar_cobrancas_diarias,
                time=time(hour=9, minute=0)
            )
            logger.info("✅ Job diário configurado")
        else:
            logger.warning("⚠️ JobQueue não disponível")
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível configurar job diário: {e}")
    
    logger.info("🤖 Bot iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
