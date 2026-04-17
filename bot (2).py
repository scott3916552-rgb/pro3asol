"""
Telegram Bot for Account Sales and Balance Management
"""
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from motor.motor_asyncio import AsyncIOMotorClient

# Load environment variables
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configuration
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME', 'telegram_bot')

# MongoDB connection with better timeout settings
mongo_client = AsyncIOMotorClient(
    MONGO_URL,
    serverSelectionTimeoutMS=10000,
    connectTimeoutMS=10000,
    socketTimeoutMS=30000,
    retryWrites=True,
    retryReads=True
)
db = mongo_client[DB_NAME]

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
(
    SYRIATEL_AMOUNT, SYRIATEL_CODE,
    SHAMCASH_AMOUNT, SHAMCASH_PROOF,
    CWALLET_AMOUNT, CWALLET_PROOF,
    COINEX_AMOUNT, COINEX_PROOF,
    BUY_QUANTITY,
    ADMIN_ADD_ACCOUNT, ADMIN_ADD_ACCOUNT_PRICE,
    ADMIN_BROADCAST, ADMIN_ADD_PRODUCT,
    ADMIN_MANUAL_BALANCE_USER, ADMIN_MANUAL_BALANCE_AMOUNT,
    ADMIN_APPROVE_AMOUNT,
) = range(16)

# Default products
DEFAULT_PRODUCTS = [
    {"name": "حسابات icloud", "key": "icloud"},
    {"name": "حسابات gmail", "key": "gmail"},
    {"name": "حسابات outlook", "key": "outlook"},
    {"name": "حسابات paypal", "key": "paypal"},
]

# ==================== Helper Functions ====================

async def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    """Get or create user in database"""
    try:
        user = await db.users.find_one({"user_id": user_id})
        if not user:
            user = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "balance": 0,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            await db.users.insert_one(user)
        return user
    except Exception as e:
        logger.error(f"Error in get_or_create_user: {e}")
        return {"user_id": user_id, "username": username, "first_name": first_name, "balance": 0}

async def update_user_balance(user_id: int, amount: int, operation: str = "add"):
    """Update user balance"""
    try:
        if operation == "add":
            await db.users.update_one(
                {"user_id": user_id},
                {"$inc": {"balance": amount}}
            )
        elif operation == "subtract":
            await db.users.update_one(
                {"user_id": user_id},
                {"$inc": {"balance": -amount}}
            )
    except Exception as e:
        logger.error(f"Error updating balance: {e}")

async def get_user_balance(user_id: int) -> int:
    """Get user balance"""
    try:
        user = await db.users.find_one({"user_id": user_id})
        return user.get("balance", 0) if user else 0
    except Exception as e:
        logger.error(f"Error getting balance: {e}")
        return 0

async def get_exchange_rate() -> int:
    """Get exchange rate (1$ = X SYP)"""
    try:
        settings = await db.settings.find_one({"key": "exchange_rate"})
        return settings.get("value", 120) if settings else 120
    except Exception as e:
        logger.error(f"Error getting exchange rate: {e}")
        return 120

async def get_all_products():
    """Get all products including default and custom"""
    products = []
    # Add default products first
    for p in DEFAULT_PRODUCTS:
        products.append({"name": p["name"], "key": p["key"]})
    
    # Add custom products from database
    try:
        custom = await db.products.find({}).to_list(100)
        for c in custom:
            # Avoid duplicates
            if c["key"] not in [p["key"] for p in products]:
                products.append({"name": c["name"], "key": c["key"]})
    except Exception as e:
        logger.error(f"Failed to fetch custom products from DB: {e}")
    
    return products

async def init_database():
    """Initialize database with default settings"""
    try:
        # Check if settings exist
        rate = await db.settings.find_one({"key": "exchange_rate"})
        if not rate:
            await db.settings.insert_one({"key": "exchange_rate", "value": 120})
        
        # Initialize default product prices
        for product in DEFAULT_PRODUCTS:
            price = await db.settings.find_one({"key": f"price_{product['key']}"})
            if not price:
                await db.settings.insert_one({"key": f"price_{product['key']}", "value": 0})
        
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

# ==================== Main Menu ====================

def get_main_menu():
    """Get main menu keyboard"""
    keyboard = [
        [InlineKeyboardButton("💰 الرصيد", callback_data="balance_menu")],
        [InlineKeyboardButton("🛒 المنتجات", callback_data="products_menu")],
        [InlineKeyboardButton("📞 تواصل مع الدعم", callback_data="support")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    await get_or_create_user(user.id, user.username, user.first_name)
    
    welcome_text = f"""
مرحباً {user.first_name}! 👋

أهلاً بك في بوت بيع الحسابات

اختر من القائمة أدناه:
"""
    await update.message.reply_text(welcome_text, reply_markup=get_main_menu())

# ==================== Balance Menu ====================

async def balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Balance menu handler"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("💳 شحن الرصيد", callback_data="recharge_menu")],
        [InlineKeyboardButton("💵 رصيدي", callback_data="my_balance")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")],
    ]
    
    await query.edit_message_text(
        "💰 قائمة الرصيد\n\nاختر من الخيارات:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user balance"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    balance = await get_user_balance(user_id)
    
    text = f"""
💵 رصيدي

💰 الرصيد الحالي: {balance:,} ل.س
🆔 معرف التلجرام: {user_id}
"""
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="balance_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== Recharge Menu ====================

async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recharge options menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📱 سيريتل كاش", callback_data="recharge_syriatel")],
        [InlineKeyboardButton("💳 شام كاش", callback_data="recharge_shamcash")],
        [InlineKeyboardButton("🔷 محفظة cwallet", callback_data="recharge_cwallet")],
        [InlineKeyboardButton("🟠 محفظة coinex", callback_data="recharge_coinex")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="balance_menu")],
    ]
    
    await query.edit_message_text(
        "💳 شحن الرصيد\n\nاختر طريقة الشحن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================== Syriatel Cash ====================

async def recharge_syriatel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Syriatel cash recharge"""
    query = update.callback_query
    await query.answer()
    
    text = """
💳 شحن الرصيد عبر سيريتل كاش (تحويل يدوي)

📱 أرقام سيريتل كاش:
0934595626
0935579034

📌 الخطوات:
1️⃣ قم بتحويل المبلغ المراد شحنه إلى أحد الأرقام أعلاه
2️⃣ بعد التحويل اضغط على زر تأكيد التحويل أدناه

⚠️ تأكد من حفظ رقم التحويلة (الكود)
"""
    keyboard = [
        [
            InlineKeyboardButton("✅ تأكيد", callback_data="syriatel_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu")
        ],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def syriatel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm syriatel transfer - ask for amount"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['recharge_method'] = 'syriatel'
    
    await query.edit_message_text("💵 قم بإدخال المبلغ الذي حولته بالليرة السورية (أدخل الرقم فقط):")
    return SYRIATEL_AMOUNT

async def syriatel_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive syriatel amount"""
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            await update.message.reply_text("❌ الرجاء إدخال مبلغ صحيح أكبر من صفر")
            return SYRIATEL_AMOUNT
        
        context.user_data['recharge_amount'] = amount
        await update.message.reply_text("🔢 أدخل رقم عملية التحويل (الكود):")
        return SYRIATEL_CODE
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح فقط")
        return SYRIATEL_AMOUNT

async def syriatel_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive syriatel transfer code"""
    code = update.message.text.strip()
    amount = context.user_data.get('recharge_amount', 0)
    user = update.effective_user
    
    # Save recharge request
    try:
        request = {
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "method": "syriatel",
            "amount": amount,
            "code": code,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        result = await db.recharge_requests.insert_one(request)
        request_id = str(result.inserted_id)
        
        # Notify admin
        admin_text = f"""
📥 طلب شحن جديد (سيريتل كاش)

👤 المستخدم: {user.first_name} (@{user.username})
🆔 ID: {user.id}
💵 المبلغ: {amount:,} ل.س
🔢 كود التحويل: {code}
"""
        admin_keyboard = [
            [
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"reject_recharge_{request_id}")
            ],
        ]
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_text,
                reply_markup=InlineKeyboardMarkup(admin_keyboard)
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
    except Exception as e:
        logger.error(f"Failed to save recharge request: {e}")
    
    await update.message.reply_text(
        "✅ تم إرسال طلب الشحن بنجاح!\n\nسيتم مراجعة طلبك قريباً، يرجى الانتظار.",
        reply_markup=get_main_menu()
    )
    
    return ConversationHandler.END

# ==================== Sham Cash ====================

async def recharge_shamcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sham cash recharge"""
    query = update.callback_query
    await query.answer()
    
    text = """
💳 شحن الرصيد عبر شام كاش على المعرف أدناه

🔗 معرف شام كاش:
bc9d9b41336308e2a4f9e0ffe86f48a0

📌 الخطوات:
1️⃣ قم بتحويل المبلغ المراد شحنه إلى المعرف أعلاه
2️⃣ بعد التحويل اضغط على زر تأكيد
"""
    keyboard = [
        [
            InlineKeyboardButton("✅ تأكيد", callback_data="shamcash_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu")
        ],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def shamcash_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm shamcash transfer"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['recharge_method'] = 'shamcash'
    
    await query.edit_message_text("💵 كم المبلغ المراد شحنه بالليرة السورية:")
    return SHAMCASH_AMOUNT

async def shamcash_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive shamcash amount"""
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            await update.message.reply_text("❌ الرجاء إدخال مبلغ صحيح أكبر من صفر")
            return SHAMCASH_AMOUNT
        
        context.user_data['recharge_amount'] = amount
        await update.message.reply_text("📸 يرجى إرسال إثبات الدفع (صورة فقط)\n\nسيتم مراجعة الطلب من الأدمن قريباً يرجى الانتظار")
        return SHAMCASH_PROOF
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح فقط")
        return SHAMCASH_AMOUNT

async def shamcash_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive shamcash proof photo"""
    if not update.message.photo:
        await update.message.reply_text("❌ الرجاء إرسال صورة فقط")
        return SHAMCASH_PROOF
    
    photo = update.message.photo[-1]  # Get highest resolution
    amount = context.user_data.get('recharge_amount', 0)
    user = update.effective_user
    
    # Save recharge request
    try:
        request = {
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "method": "shamcash",
            "amount": amount,
            "photo_file_id": photo.file_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        result = await db.recharge_requests.insert_one(request)
        request_id = str(result.inserted_id)
        
        # Notify admin
        admin_text = f"""
📥 طلب شحن جديد (شام كاش)

👤 المستخدم: {user.first_name} (@{user.username})
🆔 ID: {user.id}
💵 المبلغ: {amount:,} ل.س
"""
        admin_keyboard = [
            [
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"reject_recharge_{request_id}")
            ],
        ]
        
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo.file_id,
                caption=admin_text,
                reply_markup=InlineKeyboardMarkup(admin_keyboard)
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
    except Exception as e:
        logger.error(f"Failed to save recharge request: {e}")
    
    await update.message.reply_text(
        "✅ تم إرسال طلب الشحن بنجاح!\n\nسيتم مراجعة طلبك قريباً، يرجى الانتظار.",
        reply_markup=get_main_menu()
    )
    
    return ConversationHandler.END

# ==================== CWallet ====================

async def recharge_cwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CWallet recharge"""
    query = update.callback_query
    await query.answer()
    
    rate = await get_exchange_rate()
    
    text = f"""
💳 اشحن الرصيد عبر cwallet

على معرف محفظة cwallet حول عليه👇
THEaoFQmETNbxiouYCBhKkaYZT4Zoo8GwJ

1$={rate} ليرة سورية
"""
    keyboard = [
        [
            InlineKeyboardButton("✅ تأكيد", callback_data="cwallet_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu")
        ],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def cwallet_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm cwallet transfer"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['recharge_method'] = 'cwallet'
    rate = await get_exchange_rate()
    
    await query.edit_message_text(f"💵 كم المبلغ المراد شحنه أدخل المبلغ بالليرة السورية\n\nملاحظة: كل 1$ تساوي {rate} ليرة سورية")
    return CWALLET_AMOUNT

async def cwallet_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive cwallet amount"""
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            await update.message.reply_text("❌ الرجاء إدخال مبلغ صحيح أكبر من صفر")
            return CWALLET_AMOUNT
        
        context.user_data['recharge_amount'] = amount
        await update.message.reply_text("📸 يرجى إرسال إثبات الدفع (صورة فقط)")
        return CWALLET_PROOF
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح فقط")
        return CWALLET_AMOUNT

async def cwallet_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive cwallet proof photo"""
    if not update.message.photo:
        await update.message.reply_text("❌ الرجاء إرسال صورة فقط")
        return CWALLET_PROOF
    
    photo = update.message.photo[-1]
    amount = context.user_data.get('recharge_amount', 0)
    user = update.effective_user
    
    try:
        request = {
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "method": "cwallet",
            "amount": amount,
            "photo_file_id": photo.file_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        result = await db.recharge_requests.insert_one(request)
        request_id = str(result.inserted_id)
        
        admin_text = f"""
📥 طلب شحن جديد (cwallet)

👤 المستخدم: {user.first_name} (@{user.username})
🆔 ID: {user.id}
💵 المبلغ: {amount:,} ل.س
"""
        admin_keyboard = [
            [
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"reject_recharge_{request_id}")
            ],
        ]
        
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo.file_id,
                caption=admin_text,
                reply_markup=InlineKeyboardMarkup(admin_keyboard)
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
    except Exception as e:
        logger.error(f"Failed to save request: {e}")
    
    await update.message.reply_text(
        "✅ تم إرسال طلب الشحن بنجاح!\n\nسيتم مراجعة طلبك قريباً، يرجى الانتظار.",
        reply_markup=get_main_menu()
    )
    
    return ConversationHandler.END

# ==================== Coinex ====================

async def recharge_coinex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Coinex recharge"""
    query = update.callback_query
    await query.answer()
    
    rate = await get_exchange_rate()
    
    text = f"""
💳 اشحن الرصيد عبر coinex

على معرف محفظة coinex حول عليه👇
0xaace6d4956b27c293018556bedba49a5074d6020

أو على الإيميل مباشرةً:
km197807@gmail.com

1$={rate} ليرة سورية
"""
    keyboard = [
        [
            InlineKeyboardButton("✅ تأكيد", callback_data="coinex_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu")
        ],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def coinex_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm coinex transfer"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['recharge_method'] = 'coinex'
    rate = await get_exchange_rate()
    
    await query.edit_message_text(f"💵 كم المبلغ المراد شحنه بالليرة السورية\n\nملاحظة: كل 1$ تساوي {rate} ليرة سورية")
    return COINEX_AMOUNT

async def coinex_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive coinex amount"""
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            await update.message.reply_text("❌ الرجاء إدخال مبلغ صحيح أكبر من صفر")
            return COINEX_AMOUNT
        
        context.user_data['recharge_amount'] = amount
        await update.message.reply_text("📸 يرجى إرسال إثبات الدفع (صورة فقط)")
        return COINEX_PROOF
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح فقط")
        return COINEX_AMOUNT

async def coinex_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive coinex proof photo"""
    if not update.message.photo:
        await update.message.reply_text("❌ الرجاء إرسال صورة فقط")
        return COINEX_PROOF
    
    photo = update.message.photo[-1]
    amount = context.user_data.get('recharge_amount', 0)
    user = update.effective_user
    
    try:
        request = {
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "method": "coinex",
            "amount": amount,
            "photo_file_id": photo.file_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        result = await db.recharge_requests.insert_one(request)
        request_id = str(result.inserted_id)
        
        admin_text = f"""
📥 طلب شحن جديد (coinex)

👤 المستخدم: {user.first_name} (@{user.username})
🆔 ID: {user.id}
💵 المبلغ: {amount:,} ل.س
"""
        admin_keyboard = [
            [
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"reject_recharge_{request_id}")
            ],
        ]
        
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo.file_id,
                caption=admin_text,
                reply_markup=InlineKeyboardMarkup(admin_keyboard)
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
    except Exception as e:
        logger.error(f"Failed to save request: {e}")
    
    await update.message.reply_text(
        "✅ تم إرسال طلب الشحن بنجاح!\n\nسيتم مراجعة طلبك قريباً، يرجى الانتظار.",
        reply_markup=get_main_menu()
    )
    
    return ConversationHandler.END

# ==================== Admin Recharge Approval ====================

async def approve_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve recharge request"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ غير مصرح لك", show_alert=True)
        return
    
    request_id = query.data.split("_")[-1]
    
    from bson import ObjectId
    try:
        request = await db.recharge_requests.find_one({"_id": ObjectId(request_id)})
        
        if not request:
            await query.edit_message_text("❌ لم يتم العثور على الطلب")
            return
        
        if request.get("status") != "pending":
            await query.edit_message_text("❌ تم معالجة هذا الطلب مسبقاً")
            return
        
        # Update request status
        await db.recharge_requests.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": {"status": "approved", "approved_at": datetime.now(timezone.utc).isoformat()}}
        )
        
        # Add balance to user
        await update_user_balance(request["user_id"], request["amount"])
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=request["user_id"],
                text=f"✅ تم الموافقة على طلب الشحن!\n\n💵 تم إضافة {request['amount']:,} ل.س إلى رصيدك",
                reply_markup=get_main_menu()
            )
        except Exception as e:
            logger.error(f"Failed to notify user: {e}")
        
        await query.edit_message_text(f"✅ تم الموافقة على طلب الشحن\n\n💵 المبلغ: {request['amount']:,} ل.س\n👤 المستخدم: {request.get('first_name', 'N/A')}")
    except Exception as e:
        logger.error(f"Error approving recharge: {e}")
        await query.edit_message_text("❌ حدث خطأ أثناء معالجة الطلب")

async def reject_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject recharge request"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ غير مصرح لك", show_alert=True)
        return
    
    request_id = query.data.split("_")[-1]
    
    from bson import ObjectId
    try:
        request = await db.recharge_requests.find_one({"_id": ObjectId(request_id)})
        
        if not request:
            await query.edit_message_text("❌ لم يتم العثور على الطلب")
            return
        
        # Update request status
        await db.recharge_requests.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": {"status": "rejected", "rejected_at": datetime.now(timezone.utc).isoformat()}}
        )
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=request["user_id"],
                text="❌ تم رفض طلب الشحن\n\nيرجى التواصل مع الدعم للمزيد من المعلومات",
                reply_markup=get_main_menu()
            )
        except Exception as e:
            logger.error(f"Failed to notify user: {e}")
        
        await query.edit_message_text(f"❌ تم رفض طلب الشحن\n\n👤 المستخدم: {request.get('first_name', 'N/A')}")
    except Exception as e:
        logger.error(f"Error rejecting recharge: {e}")
        await query.edit_message_text("❌ حدث خطأ أثناء معالجة الطلب")

# ==================== Products Menu ====================

async def products_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Products menu handler"""
    query = update.callback_query
    await query.answer()
    
    try:
        products = await get_all_products()
        
        keyboard = []
        for product in products:
            keyboard.append([InlineKeyboardButton(f"🔐 {product['name']}", callback_data=f"product_{product['key']}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
        
        await query.edit_message_text(
            "🛒 المنتجات المتاحة\n\nاختر المنتج:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in products_menu: {e}")
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
        await query.edit_message_text(
            "⚠️ حدث خطأ أثناء تحميل المنتجات، يرجى المحاولة مجدداً.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show product details and purchase option"""
    query = update.callback_query
    await query.answer()
    
    product_key = query.data.replace("product_", "")
    context.user_data['selected_product'] = product_key
    
    try:
        # Get product name
        products = await get_all_products()
        product_name = next((p['name'] for p in products if p['key'] == product_key), product_key)
        
        # Get available accounts count
        accounts_count = await db.accounts.count_documents({"product_key": product_key, "sold": False})
        
        # Get price
        price_setting = await db.settings.find_one({"key": f"price_{product_key}"})
        price = price_setting.get("value", 0) if price_setting else 0
        
        text = f"""
🔐 {product_name}

📦 الكمية المتوفرة: {accounts_count}
💵 السعر: {price:,} ل.س للحساب الواحد
"""
        
        if accounts_count > 0 and price > 0:
            keyboard = [
                [InlineKeyboardButton("🛒 شراء", callback_data=f"buy_{product_key}")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="products_menu")],
            ]
        else:
            text += "\n⚠️ غير متوفر حالياً أو السعر غير محدد"
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="products_menu")]]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Error showing product: {e}")
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="products_menu")]]
        await query.edit_message_text("⚠️ حدث خطأ، يرجى المحاولة مجدداً", reply_markup=InlineKeyboardMarkup(keyboard))

async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start buying process"""
    query = update.callback_query
    await query.answer()
    
    product_key = query.data.replace("buy_", "")
    context.user_data['buying_product'] = product_key
    
    await query.edit_message_text("🔢 كم عدد الحسابات المراد شراؤها؟ (أدخل الرقم فقط)")
    return BUY_QUANTITY

async def buy_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive quantity to buy"""
    try:
        quantity = int(update.message.text.strip())
        if quantity <= 0:
            await update.message.reply_text("❌ الرجاء إدخال عدد صحيح أكبر من صفر")
            return BUY_QUANTITY
        
        product_key = context.user_data.get('buying_product')
        user_id = update.effective_user.id
        
        # Get product price
        price_setting = await db.settings.find_one({"key": f"price_{product_key}"})
        price = price_setting.get("value", 0) if price_setting else 0
        total_price = price * quantity
        
        # Check user balance
        balance = await get_user_balance(user_id)
        
        if balance < total_price:
            await update.message.reply_text(
                f"❌ رصيدك غير كافي!\n\n💵 رصيدك الحالي: {balance:,} ل.س\n💰 المطلوب: {total_price:,} ل.س",
                reply_markup=get_main_menu()
            )
            return ConversationHandler.END
        
        # Check available accounts
        available = await db.accounts.count_documents({"product_key": product_key, "sold": False})
        
        if available < quantity:
            await update.message.reply_text(
                f"❌ الكمية المطلوبة غير متوفرة!\n\n📦 المتوفر: {available} حساب",
                reply_markup=get_main_menu()
            )
            return ConversationHandler.END
        
        # Get accounts
        accounts = await db.accounts.find(
            {"product_key": product_key, "sold": False}
        ).limit(quantity).to_list(quantity)
        
        # Mark accounts as sold and deduct balance
        account_ids = [acc['_id'] for acc in accounts]
        await db.accounts.update_many(
            {"_id": {"$in": account_ids}},
            {"$set": {"sold": True, "sold_to": user_id, "sold_at": datetime.now(timezone.utc).isoformat()}}
        )
        
        await update_user_balance(user_id, total_price, "subtract")
        
        # Save purchase record
        await db.purchases.insert_one({
            "user_id": user_id,
            "product_key": product_key,
            "quantity": quantity,
            "total_price": total_price,
            "accounts": [acc.get('account_data', '') for acc in accounts],
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        
        # Send accounts to user
        products = await get_all_products()
        product_name = next((p['name'] for p in products if p['key'] == product_key), product_key)
        
        accounts_text = "\n".join([f"📧 {acc.get('account_data', 'N/A')}" for acc in accounts])
        
        await update.message.reply_text(
            f"✅ تم الشراء بنجاح!\n\n🔐 {product_name}\n📦 العدد: {quantity}\n💵 المبلغ: {total_price:,} ل.س\n\n📋 الحسابات:\n{accounts_text}",
            reply_markup=get_main_menu()
        )
        
        return ConversationHandler.END
    
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح فقط")
        return BUY_QUANTITY

# ==================== Support ====================

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support contact"""
    query = update.callback_query
    await query.answer()
    
    text = """
📞 تواصل مع الدعم

للتواصل مع الدعم اضغط على معرف الأدمن:
@km0997055
"""
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== Main Menu Return ====================

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    text = f"""
مرحباً {user.first_name}! 👋

أهلاً بك في بوت بيع الحسابات

اختر من القائمة أدناه:
"""
    await query.edit_message_text(text, reply_markup=get_main_menu())

# ==================== Admin Panel ====================

def get_admin_menu():
    """Get admin menu keyboard"""
    keyboard = [
        [InlineKeyboardButton("➕ إضافة حسابات", callback_data="admin_add_accounts")],
        [InlineKeyboardButton("📢 إرسال رسالة عامة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🆕 إضافة منتجات", callback_data="admin_add_product")],
        [InlineKeyboardButton("🗑️ حذف منتج", callback_data="admin_delete_product")],
        [InlineKeyboardButton("💰 إضافة رصيد يدوي", callback_data="admin_manual_balance")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel command"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ غير مصرح لك بالوصول")
        return
    
    await update.message.reply_text(
        "🔐 لوحة تحكم الأدمن\n\nاختر من الخيارات:",
        reply_markup=get_admin_menu()
    )

async def admin_add_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Add accounts menu - shows list of products to add accounts to"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ غير مصرح لك", show_alert=True)
        return
    
    try:
        products = await get_all_products()
        
        keyboard = []
        for product in products:
            keyboard.append([InlineKeyboardButton(f"📝 {product['name']}", callback_data=f"addacc_{product['key']}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
        
        await query.edit_message_text(
            "➕ إضافة حسابات\n\nاختر المنتج لإضافة حسابات:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in admin_add_accounts: {e}")
        await query.edit_message_text(
            "⚠️ حدث خطأ، يرجى المحاولة مجدداً",
            reply_markup=get_admin_menu()
        )

async def admin_select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Select product to add accounts"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    product_key = query.data.replace("addacc_", "")
    context.user_data['admin_adding_to_product'] = product_key
    
    # Get product name
    products = await get_all_products()
    product_name = next((p['name'] for p in products if p['key'] == product_key), product_key)
    
    await query.edit_message_text(f"📝 إضافة حساب لـ: {product_name}\n\nأدخل بيانات الحساب:")
    return ADMIN_ADD_ACCOUNT

async def admin_account_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Receive account data"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    account_data = update.message.text.strip()
    context.user_data['admin_account_data'] = account_data
    
    await update.message.reply_text("💵 أدخل سعر الحساب بالليرة السورية:")
    return ADMIN_ADD_ACCOUNT_PRICE

async def admin_account_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Receive account price and save"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    try:
        price = int(update.message.text.strip())
        product_key = context.user_data.get('admin_adding_to_product')
        account_data = context.user_data.get('admin_account_data')
        
        # Save account
        await db.accounts.insert_one({
            "product_key": product_key,
            "account_data": account_data,
            "price": price,
            "sold": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        
        # Update product price if not set
        await db.settings.update_one(
            {"key": f"price_{product_key}"},
            {"$set": {"value": price}},
            upsert=True
        )
        
        await update.message.reply_text(
            f"✅ تم إضافة الحساب بنجاح!\n\n📦 المنتج: {product_key}\n💵 السعر: {price:,} ل.س",
            reply_markup=get_admin_menu()
        )
        
        return ConversationHandler.END
    
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_ADD_ACCOUNT_PRICE

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Broadcast message menu"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.edit_message_text("📢 اكتب الرسالة المراد إرسالها لجميع المستخدمين:")
    return ADMIN_BROADCAST

async def admin_broadcast_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Send broadcast message"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    message = update.message.text.strip()
    
    # Get all users
    try:
        users = await db.users.find({}).to_list(10000)
        
        success_count = 0
        fail_count = 0
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user['user_id'],
                    text=f"📢 رسالة من الإدارة:\n\n{message}"
                )
                success_count += 1
            except Exception:
                fail_count += 1
        
        await update.message.reply_text(
            f"✅ تم إرسال الرسالة!\n\n✉️ نجح: {success_count}\n❌ فشل: {fail_count}",
            reply_markup=get_admin_menu()
        )
    except Exception as e:
        logger.error(f"Error broadcasting: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء الإرسال", reply_markup=get_admin_menu())
    
    return ConversationHandler.END

async def admin_add_product_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Add new product menu"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.edit_message_text("🆕 أدخل اسم المنتج الجديد:")
    return ADMIN_ADD_PRODUCT

async def admin_add_product_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Add new product"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    product_name = update.message.text.strip()
    product_key = product_name.replace(" ", "_").lower()
    
    try:
        # Check if exists
        existing = await db.products.find_one({"key": product_key})
        if existing:
            await update.message.reply_text(
                "❌ هذا المنتج موجود مسبقاً",
                reply_markup=get_admin_menu()
            )
            return ConversationHandler.END
        
        # Add product
        await db.products.insert_one({
            "name": product_name,
            "key": product_key,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        
        # Initialize price
        await db.settings.insert_one({"key": f"price_{product_key}", "value": 0})
        
        await update.message.reply_text(
            f"✅ تم إضافة المنتج بنجاح!\n\n📦 الاسم: {product_name}",
            reply_markup=get_admin_menu()
        )
    except Exception as e:
        logger.error(f"Error adding product: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء إضافة المنتج", reply_markup=get_admin_menu())
    
    return ConversationHandler.END

# ==================== حذف المنتجات ====================

async def admin_delete_product_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Delete product menu - عرض قائمة المنتجات للحذف"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    try:
        # جلب المنتجات المخصصة فقط (غير الافتراضية)
        custom_products = await db.products.find({}).to_list(100)
        
        if not custom_products:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")]]
            await query.edit_message_text(
                "❌ لا يوجد منتجات مخصصة للحذف\n\n⚠️ ملاحظة: لا يمكن حذف المنتجات الافتراضية (icloud, gmail, outlook, paypal)",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        keyboard = []
        for product in custom_products:
            keyboard.append([InlineKeyboardButton(f"🗑️ {product['name']}", callback_data=f"delete_product_{product['key']}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
        
        await query.edit_message_text(
            "🗑️ حذف منتج\n\n⚠️ اختر المنتج الذي تريد حذفه:\n\n(سيتم حذف المنتج وجميع الحسابات المرتبطة به)",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in delete product menu: {e}")
        await query.edit_message_text("⚠️ حدث خطأ", reply_markup=get_admin_menu())

async def admin_confirm_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Confirm delete product - تأكيد حذف المنتج"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    product_key = query.data.replace("delete_product_", "")
    context.user_data['deleting_product_key'] = product_key
    
    try:
        # جلب معلومات المنتج
        product = await db.products.find_one({"key": product_key})
        if not product:
            await query.edit_message_text(
                "❌ لم يتم العثور على المنتج",
                reply_markup=get_admin_menu()
            )
            return
        
        # حساب عدد الحسابات المرتبطة
        accounts_count = await db.accounts.count_documents({"product_key": product_key})
        
        keyboard = [
            [
                InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_delete_{product_key}"),
                InlineKeyboardButton("❌ إلغاء", callback_data="admin_delete_product")
            ]
        ]
        
        await query.edit_message_text(
            f"⚠️ تأكيد الحذف\n\n📦 المنتج: {product['name']}\n📊 عدد الحسابات المرتبطة: {accounts_count}\n\n❓ هل أنت متأكد من حذف هذا المنتج وجميع حساباته؟",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error confirming delete: {e}")
        await query.edit_message_text("⚠️ حدث خطأ", reply_markup=get_admin_menu())

async def admin_execute_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Execute delete product - تنفيذ حذف المنتج"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    product_key = query.data.replace("confirm_delete_", "")
    
    try:
        # جلب معلومات المنتج قبل الحذف
        product = await db.products.find_one({"key": product_key})
        if not product:
            await query.edit_message_text(
                "❌ لم يتم العثور على المنتج",
                reply_markup=get_admin_menu()
            )
            return
        
        product_name = product['name']
        
        # حذف جميع الحسابات المرتبطة بالمنتج
        deleted_accounts = await db.accounts.delete_many({"product_key": product_key})
        
        # حذف إعدادات السعر
        await db.settings.delete_one({"key": f"price_{product_key}"})
        
        # حذف المنتج
        await db.products.delete_one({"key": product_key})
        
        await query.edit_message_text(
            f"✅ تم حذف المنتج بنجاح!\n\n📦 المنتج: {product_name}\n🗑️ الحسابات المحذوفة: {deleted_accounts.deleted_count}",
            reply_markup=get_admin_menu()
        )
    except Exception as e:
        logger.error(f"Error deleting product: {e}")
        await query.edit_message_text("⚠️ حدث خطأ أثناء الحذف", reply_markup=get_admin_menu())

# ==================== إضافة رصيد يدوي ====================

async def admin_manual_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Manual balance menu"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.edit_message_text("👤 أدخل رقم ID المستخدم الذي تريد إضافة رصيد له:")
    return ADMIN_MANUAL_BALANCE_USER

async def admin_manual_balance_user_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Receive user ID for manual balance"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    try:
        user_id = int(update.message.text.strip())
        
        # Check if user exists
        user = await db.users.find_one({"user_id": user_id})
        if not user:
            await update.message.reply_text(
                "❌ لم يتم العثور على المستخدم",
                reply_markup=get_admin_menu()
            )
            return ConversationHandler.END
        
        context.user_data['admin_balance_user_id'] = user_id
        await update.message.reply_text(f"💵 المستخدم: {user.get('first_name', 'N/A')}\n\nأدخل المبلغ المراد إضافته بالليرة السورية:")
        return ADMIN_MANUAL_BALANCE_AMOUNT
    
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_MANUAL_BALANCE_USER

async def admin_manual_balance_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Add manual balance"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    try:
        amount = int(update.message.text.strip())
        user_id = context.user_data.get('admin_balance_user_id')
        
        await update_user_balance(user_id, amount)
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ تم إضافة {amount:,} ل.س إلى رصيدك من قبل الإدارة"
            )
        except Exception:
            pass
        
        await update.message.reply_text(
            f"✅ تم إضافة الرصيد بنجاح!\n\n👤 المستخدم ID: {user_id}\n💵 المبلغ: {amount:,} ل.س",
            reply_markup=get_admin_menu()
        )
        
        return ConversationHandler.END
    
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_MANUAL_BALANCE_AMOUNT

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to admin menu"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.edit_message_text(
        "🔐 لوحة تحكم الأدمن\n\nاختر من الخيارات:",
        reply_markup=get_admin_menu()
    )

# ==================== Cancel Handler ====================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    await update.message.reply_text(
        "❌ تم الإلغاء",
        reply_markup=get_main_menu()
    )
    return ConversationHandler.END

# ==================== Main Application ====================

async def post_init(application) -> None:
    """Initialize database after bot starts"""
    await init_database()
    logger.info("Bot initialized successfully!")

def main():
    """Main function to run the bot"""
    # Create application
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    # Syriatel conversation handler
    syriatel_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(syriatel_confirm, pattern="^syriatel_confirm$")],
        states={
            SYRIATEL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, syriatel_amount_received)],
            SYRIATEL_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, syriatel_code_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Shamcash conversation handler
    shamcash_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(shamcash_confirm, pattern="^shamcash_confirm$")],
        states={
            SHAMCASH_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, shamcash_amount_received)],
            SHAMCASH_PROOF: [MessageHandler(filters.PHOTO, shamcash_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # CWallet conversation handler
    cwallet_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(cwallet_confirm, pattern="^cwallet_confirm$")],
        states={
            CWALLET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cwallet_amount_received)],
            CWALLET_PROOF: [MessageHandler(filters.PHOTO, cwallet_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Coinex conversation handler
    coinex_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(coinex_confirm, pattern="^coinex_confirm$")],
        states={
            COINEX_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, coinex_amount_received)],
            COINEX_PROOF: [MessageHandler(filters.PHOTO, coinex_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Buy conversation handler
    buy_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_product, pattern="^buy_")],
        states={
            BUY_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_quantity_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Admin add account conversation handler
    admin_add_account_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_select_product, pattern="^addacc_")],
        states={
            ADMIN_ADD_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_account_received)],
            ADMIN_ADD_ACCOUNT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_account_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_back, pattern="^admin_back$"),
        ],
        allow_reentry=True,
    )
    
    # Admin broadcast conversation handler
    admin_broadcast_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast, pattern="^admin_broadcast$")],
        states={
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Admin add product conversation handler
    admin_add_product_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_product_menu, pattern="^admin_add_product$")],
        states={
            ADMIN_ADD_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_product_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Admin manual balance conversation handler
    admin_manual_balance_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_manual_balance, pattern="^admin_manual_balance$")],
        states={
            ADMIN_MANUAL_BALANCE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manual_balance_user_received)],
            ADMIN_MANUAL_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manual_balance_amount_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    
    # Add conversation handlers
    application.add_handler(syriatel_handler)
    application.add_handler(shamcash_handler)
    application.add_handler(cwallet_handler)
    application.add_handler(coinex_handler)
    application.add_handler(buy_handler)
    application.add_handler(admin_add_account_handler)
    application.add_handler(admin_broadcast_handler)
    application.add_handler(admin_add_product_handler)
    application.add_handler(admin_manual_balance_handler)
    
    # Add callback query handlers
    application.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(balance_menu, pattern="^balance_menu$"))
    application.add_handler(CallbackQueryHandler(my_balance, pattern="^my_balance$"))
    application.add_handler(CallbackQueryHandler(recharge_menu, pattern="^recharge_menu$"))
    application.add_handler(CallbackQueryHandler(recharge_syriatel, pattern="^recharge_syriatel$"))
    application.add_handler(CallbackQueryHandler(recharge_shamcash, pattern="^recharge_shamcash$"))
    application.add_handler(CallbackQueryHandler(recharge_cwallet, pattern="^recharge_cwallet$"))
    application.add_handler(CallbackQueryHandler(recharge_coinex, pattern="^recharge_coinex$"))
    application.add_handler(CallbackQueryHandler(products_menu, pattern="^products_menu$"))
    application.add_handler(CallbackQueryHandler(show_product, pattern="^product_"))
    application.add_handler(CallbackQueryHandler(support, pattern="^support$"))
    application.add_handler(CallbackQueryHandler(approve_recharge, pattern="^approve_recharge_"))
    application.add_handler(CallbackQueryHandler(reject_recharge, pattern="^reject_recharge_"))
    application.add_handler(CallbackQueryHandler(admin_add_accounts, pattern="^admin_add_accounts$"))
    application.add_handler(CallbackQueryHandler(admin_delete_product_menu, pattern="^admin_delete_product$"))
    application.add_handler(CallbackQueryHandler(admin_confirm_delete_product, pattern="^delete_product_"))
    application.add_handler(CallbackQueryHandler(admin_execute_delete_product, pattern="^confirm_delete_"))
    application.add_handler(CallbackQueryHandler(admin_back, pattern="^admin_back$"))
    
    # Run the bot
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
