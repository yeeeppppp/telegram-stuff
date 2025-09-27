import subprocess
import random
import string
import logging
import json
import os
import asyncio
import requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "token"
DB_FILE = "DB.json"
SUDO_USER = os.getenv('SUDO_USER', 'admin')
PAYPAL_CLIENT_ID = "AZdNcDSqSdB0c3fA6aVk6x9JqYl-6YRC2nkXDJO6l0u0-1VT95ZaWzxKPzyRhqCGObkN3Jbvap94FCQr"
PAYPAL_SECRET = "ELsSYnrnYfHZsTrrwWKmA7TMsI1IVHFFRA2LVh82ZK3kFBnAHuNFN5S7Wtq-pBjQY5bIEI-bTWj7_Dbs"
PAYPAL_MODE = "sandbox"  # "sandbox" для тестов, "live" для продакшена

class PayPalClient:
    BASE_URL = {
        "sandbox": "https://api.sandbox.paypal.com",
        "live": "https://api.paypal.com"
    }

    @classmethod
    def get_access_token(cls):
        url = f"{cls.BASE_URL[PAYPAL_MODE]}/v1/oauth2/token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}
        response = requests.post(url, headers=headers, data=data, auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET))
        if response.status_code == 200:
            return response.json().get("access_token")
        logger.error(f"PayPal auth error: {response.text}")
        return None

    @classmethod
    def create_order(cls, amount, currency, description):
        url = f"{cls.BASE_URL[PAYPAL_MODE]}/v2/checkout/orders"
        access_token = cls.get_access_token()
        if not access_token:
            return None

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "PayPal-Request-Id": f"ORDER-{random.randint(100000, 999999)}"
        }
        
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {
                    "currency_code": currency,
                    "value": str(amount)
                },
                "description": description
            }]
        }
        
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 201:
            return response.json()
        logger.error(f"PayPal create order error: {response.text}")
        return None

    @classmethod
    def capture_order(cls, order_id):
        url = f"{cls.BASE_URL[PAYPAL_MODE]}/v2/checkout/orders/{order_id}/capture"
        access_token = cls.get_access_token()
        if not access_token:
            return None

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }
        
        response = requests.post(url, headers=headers)
        if response.status_code == 201:
            return response.json()
        logger.error(f"PayPal capture error: {response.text}")
        return None

    @classmethod
    def get_order_details(cls, order_id):
        url = f"{cls.BASE_URL[PAYPAL_MODE]}/v2/checkout/orders/{order_id}"
        access_token = cls.get_access_token()
        if not access_token:
            return None

        headers = {
            "Authorization": f"Bearer {access_token}"
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        logger.error(f"PayPal get order error: {response.text}")
        return None

class Database:
    @staticmethod
    def load():
        try:
            if not os.path.exists(DB_FILE):
                return Database.initialize_db()
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки БД: {e}")
            return Database.initialize_db()

    @staticmethod
    def initialize_db():
        default_data = {
            "users": [],
            "purchase_options": {
                "1m": {"Stripe_EUR": 2, "Litecoin_LTC": 0.004, "comment": "1month subscription"},
                "2m": {"Stripe_EUR": 4, "Litecoin_LTC": 0.008, "comment": "2month subscription"},
                "3m": {"Stripe_EUR": 5, "Litecoin_LTC": 0.010, "comment": "3month subscription"},
                "6m": {"Stripe_EUR": 8, "Litecoin_LTC": 0.016, "comment": "6month subscription"},
                "1y": {"Stripe_EUR": 10, "Litecoin_LTC": 0.020, "comment": "1year subscription"},
                "5y": {"Stripe_EUR": 40, "Litecoin_LTC": 0.080, "comment": "5year subscription"}
            },
            "coupons": {
                "freeweek": {"quantity": 2, "TimeLength": "1w"},
                "freemonth": {"quantity": 2, "TimeLength": "1m"}
            }
        }
        Database.save(default_data)
        return default_data

    @staticmethod
    def save(data):
        try:
            with open(DB_FILE, 'w') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения БД: {e}")

    @staticmethod
    def get_user(user_id: str):
        db = Database.load()
        return next((u for u in db["users"] if u["user_id"] == user_id), None)

    @staticmethod
    def update_user(user_data: dict):
        db = Database.load()
        for i, user in enumerate(db["users"]):
            if user["user_id"] == user_data["user_id"]:
                db["users"][i] = user_data
                break
        else:
            db["users"].append(user_data)
        Database.save(db)

    @staticmethod
    def remove_user(user_id: str):
        db = Database.load()
        db["users"] = [u for u in db["users"] if u["user_id"] != user_id]
        Database.save(db)

    @staticmethod
    def add_payment(user_id: str, order_id: str, plan: str, amount: float, currency: str = "EUR"):
        db = Database.load()
        if "payments" not in db:
            db["payments"] = {}
            
        db["payments"][order_id] = {
            "user_id": user_id,
            "plan": plan,
            "amount": amount,
            "currency": currency,
            "status": "CREATED",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        Database.save(db)

    @staticmethod
    def update_payment_status(order_id: str, status: str):
        db = Database.load()
        if "payments" in db and order_id in db["payments"]:
            db["payments"][order_id]["status"] = status
            db["payments"][order_id]["updated_at"] = datetime.now().isoformat()
            Database.save(db)
            return True
        return False

    @staticmethod
    def get_payment(order_id: str):
        db = Database.load()
        return db.get("payments", {}).get(order_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.message.from_user
        user_id = str(user.id)
        
        keyboard = [
            [InlineKeyboardButton("English", callback_data="en"),
             InlineKeyboardButton("Русский", callback_data="ru")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        db_user = Database.get_user(user_id)
        if not db_user:
            db_user = {
                "user_id": user_id,
                "sshName": "",
                "sshPassword": "",
                "TGname": user.username or "NoUsername",
                "expire_datetime": "",
                "language": "en"
            }
            Database.update_user(db_user)
        
        await update.message.reply_text(
            "Choose language: / Выберите язык:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    try:
        lang = query.data
        user_id = str(query.from_user.id)
        db_user = Database.get_user(user_id)
        
        if not db_user:
            db_user = {
                "user_id": user_id,
                "sshName": "",
                "sshPassword": "",
                "TGname": query.from_user.username or "NoUsername",
                "expire_datetime": "",
                "language": lang
            }
        else:
            db_user["language"] = lang
        
        Database.update_user(db_user)
        
        response = {
            "en": "Language set to English!",
            "ru": "Язык установлен на Русский!"
        }.get(lang, "Language set!")
        await query.edit_message_text(response)
        
        welcome_msg = {
            "en": f"Welcome {db_user['TGname']}! Use /subscribe to see plans.",
            "ru": f"Добро пожаловать {db_user['TGname']}! Используйте /subscribe для просмотра планов."
        }.get(lang, "Welcome!")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=welcome_msg
        )
        
    except Exception as e:
        logger.error(f"Ошибка в set_language: {e}")
        await query.edit_message_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        
        messages = {
            "en": (
                "/start - Welcome\n"
                "/help - Show commands\n"
                "/subscribe - Buy/renew\n"
                "/status - Check subscription\n"
                "/extend - Extend\n"
                "/cancel - Remove access\n"
                "/serverinfo - SSH details\n"
                "/contact - Report issues\n"
                "/coupon - Use free codes\n"
                "/pay - Subscription payment\n"
                "/check_payment - Get yours payment status"
            ),
            "ru": (
                "/start - Приветствие\n"
                "/help - Список команд\n"
                "/subscribe - Покупка/продление\n"
                "/status - Статус подписки\n"
                "/extend - Продлить\n"
                "/cancel - Удалить доступ\n"
                "/serverinfo - SSH данные\n"
                "/contact - Сообщить проблему\n"
                "/coupon - Использовать кодов\n"
                "/pay - Оплата подписки\n"
                "/check_payment - Проверка статуса оплаты"
            )
        }
        
        await update.message.reply_text(messages.get(lang, messages["en"]))
        
    except Exception as e:
        logger.error(f"Ошибка в /help: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        db = Database.load()
        options = db["purchase_options"]

        messages = {
            "en": {
                "header": "📊 Available subscription plans:\n\n",
                "item": "• {plan}: ${price} {currency} ({comment})\n",
                "footer": "\nUse /pay <plan> to purchase (e.g. /pay 1m)"
            },
            "ru": {
                "header": "📊 Доступные планы подписки:\n\n",
                "item": "• {plan}: ${price} {currency} ({comment})\n",
                "footer": "\nИспользуйте /pay <план> для покупки (напр. /pay 1m)"
            }
        }

        lang_templates = messages.get(lang, messages["en"])

        msg = lang_templates["header"]
        
        for plan, details in options.items():
            msg += lang_templates["item"].format(
                plan=plan,
                price=details['Stripe_EUR'],
                currency="EUR",
                comment=details['comment']
            )
        
        msg += lang_templates["footer"]
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Ошибка в /subscribe: {str(e)}", exc_info=True)
        error_msg = {
            "en": "⚠️ Error loading subscription plans. Please try later.",
            "ru": "⚠️ Ошибка загрузки планов подписки. Попробуйте позже."
        }
        await update.message.reply_text(error_msg.get(lang, "Payment error"))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        
        active = False
        if db_user and db_user.get("expire_datetime"):
            expire_date = datetime.fromisoformat(db_user["expire_datetime"])
            if expire_date > datetime.now():
                active = True
                message = {
                    "en": f"Active until: {db_user['expire_datetime']}",
                    "ru": f"Активно до: {db_user['expire_datetime']}"
                }.get(lang, f"Active until: {db_user['expire_datetime']}")
        
        if not active:
            message = {
                "en": "No active subscription.",
                "ru": "Нет активной подписки."
            }.get(lang, "No active subscription.")
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Ошибка в /status: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def extend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        
        active = False
        if db_user and db_user.get("expire_datetime"):
            expire_date = datetime.fromisoformat(db_user["expire_datetime"])
            if expire_date > datetime.now():
                active = True
        
        if active:
            message = {
                "en": "Send 'payment confirmed' with plan (e.g., 'payment confirmed 3m') to extend.",
                "ru": "Отправьте 'payment confirmed' с планом (например, 'payment confirmed 3m') для продления."
            }.get(lang, "Send 'payment confirmed' with plan to extend.")
        else:
            message = {
                "en": "No active subscription to extend. Use /subscribe.",
                "ru": "Нет активной подписки для продления. Используйте /subscribe."
            }.get(lang, "No active subscription to extend. Use /subscribe.")
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Ошибка в /extend: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        
        if update.message.text.lower() == "iknowwhatiamdoing":
            if db_user and db_user["sshName"]:
                ssh_name = db_user["sshName"]
                
                subprocess.run(["sudo", "usermod", "-p", "!", ssh_name], check=True)
                
                home_dir = f"/home/{ssh_name}"
                expired_dir = f"/home/{SUDO_USER}/expiredusers/{ssh_name}"
                os.makedirs(os.path.dirname(expired_dir), exist_ok=True)
                
                if os.path.exists(home_dir):
                    subprocess.run(["sudo", "mv", home_dir, expired_dir], check=True)
                
                with open(f"{expired_dir}/user_info.json", 'w') as f:
                    json.dump(db_user, f, indent=4)
                
                Database.remove_user(user_id)
                
                message = {
                    "en": "Access moved to expired.",
                    "ru": "Доступ перемещен в expired."
                }.get(lang, "Access moved to expired.")
            else:
                message = {
                    "en": "No active account to cancel.",
                    "ru": "Нет активного аккаунта для отмены."
                }.get(lang, "No active account to cancel.")
        else:
            message = {
                "en": "Type exactly 'iKnowWhatIamDoing' to cancel.",
                "ru": "Введите точно 'iKnowWhatIamDoing' для отмены."
            }.get(lang, "Type exactly 'iKnowWhatIamDoing' to cancel.")
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Ошибка в /cancel: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def serverinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        
        active = False
        if db_user and db_user.get("expire_datetime"):
            expire_date = datetime.fromisoformat(db_user["expire_datetime"])
            if expire_date > datetime.now():
                active = True
                ssh_name = db_user["sshName"]

                ip = subprocess.check_output(
                    "ip addr | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | cut -d/ -f1 | head -n1",
                    shell=True
                ).decode().strip()
                
                message = {
                    "en": f"IP: {ip}\nPort: 33\nUser: {ssh_name}\nExpiry: {db_user['expire_datetime']}",
                    "ru": f"IP: {ip}\nПорт: 33\nПользователь: {ssh_name}\nИстекает: {db_user['expire_datetime']}"
                }.get(lang, f"IP: {ip}\nPort: 33\nUser: {ssh_name}\nExpiry: {db_user['expire_datetime']}")
        
        if not active:
            message = {
                "en": "No active subscription.",
                "ru": "Нет активной подписки."
            }.get(lang, "No active subscription.")
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Ошибка в /serverinfo: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        
        message = {
            "en": "Send your username and problem description for admin.",
            "ru": "Отправьте имя пользователя и описание проблемы для администратора."
        }.get(lang, "Send your username and problem description for admin.")
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Ошибка в /contact: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def coupon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        db = Database.load()
        
        coupon_keys = ", ".join(db["coupons"].keys())
        
        message = {
            "en": f"Codes: {coupon_keys} - check quantities and durations in admin.",
            "ru": f"Коды: {coupon_keys} - проверьте количество и длительность у админа."
        }.get(lang, f"Codes: {coupon_keys} - check quantities and durations in admin.")
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Ошибка в /coupon: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        text = update.message.text.lower()
        
        if text.startswith('payment confirmed'):
            context.user_data['payment_confirmed'] = True
            
            parts = text.split()
            if len(parts) >= 3:
                plan = parts[2]
                db = Database.load()
                if plan in db["purchase_options"]:
                    context.user_data['selected_plan'] = plan
            
            db_user = Database.get_user(user_id)
            lang = db_user["language"] if db_user else "en"
            
            message = {
                "en": "Payment confirmed! Use /subscribe or /extend.",
                "ru": "Оплата подтверждена! Используйте /subscribe или /extend."
            }.get(lang, "Payment confirmed! Use /subscribe or /extend.")
            
            await update.message.reply_text(message)
        else:
            db_user = Database.get_user(user_id)
            lang = db_user["language"] if db_user else "en"
            
            message = {
                "en": "Invalid. Send 'payment confirmed' with plan (e.g., 'payment confirmed 3m').",
                "ru": "Неверно. Отправьте 'payment confirmed' с планом (например, 'payment confirmed 3m')."
            }.get(lang, "Invalid. Send 'payment confirmed' with plan.")
            
            await update.message.reply_text(message)
            
    except Exception as e:
        logger.error(f"Ошибка в confirm_payment: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")


async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        db_user = Database.get_user(user_id)
        lang = db_user["language"] if db_user else "en"
        
        if not context.args:
            message = {
                "en": "Please specify a plan (e.g., /pay 1m)",
                "ru": "Укажите план (например, /pay 1m)"
            }.get(lang, "Please specify a plan")
            await update.message.reply_text(message)
            return
            
        plan = context.args[0].lower()
        db = Database.load()
        if plan not in db["purchase_options"]:
            message = {
                "en": "Invalid plan. Use /subscribe to see available plans.",
                "ru": "Неверный план. Используйте /subscribe для просмотра доступных планов."
            }.get(lang, "Invalid plan")
            await update.message.reply_text(message)
            return

        plan_details = db["purchase_options"][plan]
        amount = plan_details["Stripe_EUR"]
        description = plan_details["comment"]

        order = PayPalClient.create_order(amount, "EUR", description)
        if not order:
            message = {
                "en": "Payment service is unavailable. Please try again later.",
                "ru": "Платежный сервис недоступен. Пожалуйста, попробуйте позже."
            }.get(lang, "Payment service unavailable")
            await update.message.reply_text(message)
            return
            
        order_id = order["id"]
        approval_url = next(
            (link["href"] for link in order["links"] if link["rel"] == "approve"),
            None
        )
        
        if not approval_url:
            logger.error(f"No approval URL in PayPal response: {order}")
            message = {
                "en": "Payment error. Please try again later.",
                "ru": "Ошибка платежа. Пожалуйста, попробуйте позже."
            }.get(lang, "Payment error")
            await update.message.reply_text(message)
            return

        Database.add_payment(user_id, order_id, plan, amount)

        message = {
            "en": f"🔗 Please complete your payment: {approval_url}\n\nAfter payment, use /check_payment {order_id} to activate your subscription.",
            "ru": f"🔗 Пожалуйста, завершите оплату: {approval_url}\n\nПосле оплаты используйте /check_payment {order_id} для активации подписки."
        }.get(lang, f"Complete payment: {approval_url}\n\nAfter payment, use /check_payment {order_id}")
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Error in /pay: {e}")
        await update.message.reply_text("⚠️ An error occurred. Please try again.")

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)

        if not context.args:
            message = "Please specify payment ID (e.g., /check_payment ORDER-123)"
            await update.message.reply_text(message)
            return
            
        order_id = context.args[0]
        payment_info = Database.get_payment(order_id)
        
        if not payment_info or payment_info["user_id"] != user_id:
            message = "Payment not found or you don't have permission to check it."
            await update.message.reply_text(message)
            return
            
        order_details = PayPalClient.get_order_details(order_id)
        if not order_details:
            message = "Failed to get payment status. Please try again later."
            await update.message.reply_text(message)
            return
            
        status = order_details.get("status", "UNKNOWN").upper()
        Database.update_payment_status(order_id, status)

        if status == "COMPLETED":
            plan = payment_info["plan"]
            days = {'1m': 30, '2m': 60, '3m': 90, '6m': 180, '1y': 365, '5y': 1825}.get(plan, 30)
            expiry = (datetime.now() + timedelta(days=days)).isoformat()

            db_user = Database.get_user(user_id)
            if db_user:
                db_user["expire_datetime"] = expiry
                Database.update_user(db_user)
            
            message = f"✅ Payment confirmed! Your {plan} subscription is now active."
            await update.message.reply_text(message)
            
        elif status == "APPROVED":
            capture_result = PayPalClient.capture_order(order_id)
            if capture_result and capture_result.get("status") == "COMPLETED":
                Database.update_payment_status(order_id, "COMPLETED")
                message = "✅ Payment captured! Subscription activated."
            else:
                message = "⚠️ Payment capture failed. Please contact support."
            await update.message.reply_text(message)
            
        else:
            message = f"ℹ️ Payment status: {status}. Please wait or contact support."
            await update.message.reply_text(message)
            
    except Exception as e:
        logger.error(f"Error in /check_payment: {e}")
        await update.message.reply_text("⚠️ An error occurred. Please try again.")

async def check_expiry(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        logger.info("Проверка истекающих подписок...")
        db = Database.load()
        current_time = datetime.now()
        expired_users = []
        
        for user in db["users"]:
            if user.get("expire_datetime"):
                try:
                    expire_date = datetime.fromisoformat(user["expire_datetime"])
                    if expire_date <= current_time and user["sshName"]:
                        ssh_name = user["sshName"]
                        
                        subprocess.run(["sudo", "usermod", "-p", "!", ssh_name], check=True)
                        
                        home_dir = f"/home/{ssh_name}"
                        expired_dir = f"/home/{SUDO_USER}/expiredusers/{ssh_name}"
                        os.makedirs(os.path.dirname(expired_dir), exist_ok=True)
                        
                        if os.path.exists(home_dir):
                            subprocess.run(["sudo", "mv", home_dir, expired_dir], check=True)
                        
                        with open(f"{expired_dir}/user_info.json", 'w') as f:
                            json.dump(user, f, indent=4)
                        
                        expired_users.append(user)
                        logger.info(f"Подписка пользователя {ssh_name} истекла")
                except Exception as e:
                    logger.error(f"Ошибка обработки пользователя {user['user_id']}: {e}")
        
        if expired_users:
            user_ids = [u["user_id"] for u in expired_users]
            db["users"] = [u for u in db["users"] if u["user_id"] not in user_ids]
            Database.save(db)
            logger.info(f"Удалено {len(expired_users)} истекших подписок")
        
    except Exception as e:
        logger.error(f"Ошибка в check_expiry: {e}")

def main() -> None:
    """Запуск бота"""
    try:
        application = Application.builder().token(TOKEN).build()
        
        handlers = [
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("subscribe", subscribe),
            CommandHandler("status", status),
            CommandHandler("extend", extend),
            CommandHandler("cancel", cancel),
            CommandHandler("serverinfo", serverinfo),
            CommandHandler("contact", contact),
            CommandHandler("coupon", coupon),
            CommandHandler("pay", pay),
            CommandHandler("check_payment", check_payment),
            MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_payment),
            CallbackQueryHandler(set_language)
        ]
        
        for handler in handlers:
            application.add_handler(handler)
        
        job_queue = application.job_queue
        if job_queue:
            job_queue.run_repeating(
                check_expiry,
                interval=3600,
                first=10
            )
        
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}")

if __name__ == "__main__":
    if not os.path.exists(DB_FILE):
        Database.initialize_db()
    
    main()