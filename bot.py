"""
Telegram Bot for Account Sales - Fixed Version
"""
import os
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from bson import ObjectId

# ── Configuration ──────────────────────────────────────────────────────────────
# Try loading .env if it exists (optional – Replit Secrets take priority)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID       = int(os.environ.get('ADMIN_ID', '0'))
MONGO_URL      = os.environ.get('MONGO_URL')
DB_NAME        = os.environ.get('DB_NAME', 'telegram_bot')

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── MongoDB ────────────────────────────────────────────────────────────────────
mongo_client = None
db           = None

async def init_mongo():
    """Initialize MongoDB connection and verify it with a ping."""
    global mongo_client, db
    if not MONGO_URL:
        logger.error("MONGO_URL is not set – database features will be disabled")
        return False
    try:
        mongo_client = AsyncIOMotorClient(
            MONGO_URL,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
        )
        # Verify connection immediately so we fail fast instead of on first use
        await mongo_client.admin.command('ping')
        db = mongo_client[DB_NAME]
        logger.info("MongoDB connected successfully")
        return True
    except Exception as e:
        logger.error(f"MongoDB connection error: {e}")
        return False

# ── Conversation states ────────────────────────────────────────────────────────
(
    SYRIATEL_AMOUNT, SYRIATEL_CODE,
    SHAMCASH_AMOUNT, SHAMCASH_PROOF,
    CWALLET_AMOUNT,  CWALLET_PROOF,
    COINEX_AMOUNT,   COINEX_PROOF,
    BUY_QUANTITY,
    ADMIN_ADD_ACCOUNT, ADMIN_ADD_ACCOUNT_PRICE,
    ADMIN_BROADCAST, ADMIN_ADD_PRODUCT,
    ADMIN_MANUAL_BALANCE_USER, ADMIN_MANUAL_BALANCE_AMOUNT,
) = range(15)

# ── Default products ───────────────────────────────────────────────────────────
DEFAULT_PRODUCTS = [
    {"name": "حسابات icloud",  "key": "icloud"},
    {"name": "حسابات gmail",   "key": "gmail"},
    {"name": "حسابات outlook", "key": "outlook"},
    {"name": "حسابات paypal",  "key": "paypal"},
]

def get_default_products():
    return DEFAULT_PRODUCTS.copy()

async def get_custom_products():
    try:
        if db is None:
            return []
        custom = await db.products.find({"key": {"$exists": True}}).to_list(100)
        valid = []
        for c in custom:
            key = c.get("key", "")
            if key and len(key) <= 32 and key.isascii():
                valid.append({"name": c["name"], "key": key})
            elif key:
                safe_key = "".join(ch for ch in key if ch.isalnum() or ch == "_")[:32]
                if safe_key:
                    valid.append({"name": c["name"], "key": safe_key})
        return valid
    except Exception as e:
        logger.error(f"Error fetching custom products: {e}")
        return []

async def get_all_products():
    products = get_default_products()
    products.extend(await get_custom_products())
    return products

# ── DB helpers ─────────────────────────────────────────────────────────────────

async def db_find_one(collection, query):
    try:
        if db is None:
            return None
        return await db[collection].find_one(query)
    except Exception as e:
        logger.error(f"db_find_one ({collection}): {e}")
        return None

async def db_insert_one(collection, document):
    try:
        if db is None:
            logger.error("DB not connected – insert skipped")
            return None
        return await db[collection].insert_one(document)
    except Exception as e:
        logger.error(f"db_insert_one ({collection}): {e}")
        return None

async def db_update_one(collection, query, update, upsert=False):
    try:
        if db is None:
            return None
        return await db[collection].update_one(query, update, upsert=upsert)
    except Exception as e:
        logger.error(f"db_update_one ({collection}): {e}")
        return None

async def db_delete_one(collection, query):
    try:
        if db is None:
            return None
        return await db[collection].delete_one(query)
    except Exception as e:
        logger.error(f"db_delete_one ({collection}): {e}")
        return None

async def db_delete_many(collection, query):
    try:
        if db is None:
            return None
        return await db[collection].delete_many(query)
    except Exception as e:
        logger.error(f"db_delete_many ({collection}): {e}")
        return None

async def db_count(collection, query):
    try:
        if db is None:
            return 0
        return await db[collection].count_documents(query)
    except Exception as e:
        logger.error(f"db_count ({collection}): {e}")
        return 0

async def db_find(collection, query, limit=100):
    try:
        if db is None:
            return []
        return await db[collection].find(query).to_list(limit)
    except Exception as e:
        logger.error(f"db_find ({collection}): {e}")
        return []

# ── User helpers ───────────────────────────────────────────────────────────────

async def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    user = await db_find_one("users", {"user_id": user_id})
    if not user:
        user = {
            "user_id":    user_id,
            "username":   username,
            "first_name": first_name,
            "balance":    0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db_insert_one("users", user)
    return user

async def get_user_balance(user_id: int) -> int:
    user = await db_find_one("users", {"user_id": user_id})
    return user.get("balance", 0) if user else 0

async def update_user_balance(user_id: int, amount: int, operation: str = "add"):
    delta = amount if operation == "add" else -amount
    await db_update_one("users", {"user_id": user_id}, {"$inc": {"balance": delta}})

async def get_exchange_rate() -> int:
    s = await db_find_one("settings", {"key": "exchange_rate"})
    return s.get("value", 120) if s else 120

async def get_product_price(product_key: str) -> int:
    s = await db_find_one("settings", {"key": f"price_{product_key}"})
    return s.get("value", 0) if s else 0

# ── Keyboards ──────────────────────────────────────────────────────────────────

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 الرصيد",         callback_data="balance_menu")],
        [InlineKeyboardButton("🛒 المنتجات",        callback_data="products_menu")],
        [InlineKeyboardButton("📞 تواصل مع الدعم", callback_data="support")],
    ])

def get_admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة حسابات",       callback_data="admin_add_accounts")],
        [InlineKeyboardButton("📢 إرسال رسالة عامة",  callback_data="admin_broadcast")],
        [InlineKeyboardButton("🆕 إضافة منتجات",       callback_data="admin_add_product")],
        [InlineKeyboardButton("🗑️ حذف منتج",          callback_data="admin_delete_product")],
        [InlineKeyboardButton("💰 إضافة رصيد يدوي",   callback_data="admin_manual_balance")],
    ])

# ── Commands ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await get_or_create_user(user.id, user.username, user.first_name)
    await update.message.reply_text(
        f"مرحباً {user.first_name}! 👋\n\nأهلاً بك في بوت بيع الحسابات\n\nاختر من القائمة أدناه:",
        reply_markup=get_main_menu()
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ غير مصرح لك بالوصول")
        return
    await update.message.reply_text(
        "🔐 لوحة تحكم الأدمن\n\nاختر من الخيارات:",
        reply_markup=get_admin_menu()
    )

# ── Menu handlers ──────────────────────────────────────────────────────────────

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"مرحباً {query.from_user.first_name}! 👋\n\nأهلاً بك في بوت بيع الحسابات\n\nاختر من القائمة أدناه:",
        reply_markup=get_main_menu()
    )

async def balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💰 قائمة الرصيد\n\nاختر من الخيارات:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 شحن الرصيد", callback_data="recharge_menu")],
            [InlineKeyboardButton("💵 رصيدي",      callback_data="my_balance")],
            [InlineKeyboardButton("🔙 رجوع",       callback_data="main_menu")],
        ])
    )

async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    balance = await get_user_balance(query.from_user.id)
    await query.edit_message_text(
        f"💵 رصيدي\n\n💰 الرصيد الحالي: {balance:,} ل.س\n🆔 معرف التلجرام: {query.from_user.id}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="balance_menu")]])
    )

async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💳 شحن الرصيد\n\nاختر طريقة الشحن:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 سيريتل كاش",   callback_data="recharge_syriatel")],
            [InlineKeyboardButton("💳 شام كاش",       callback_data="recharge_shamcash")],
            [InlineKeyboardButton("🔷 محفظة cwallet", callback_data="recharge_cwallet")],
            [InlineKeyboardButton("🟠 محفظة coinex",  callback_data="recharge_coinex")],
            [InlineKeyboardButton("🔙 رجوع",          callback_data="balance_menu")],
        ])
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📞 تواصل مع الدعم\n\nللتواصل مع الدعم اضغط على معرف الأدمن:\n@km0997055",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]])
    )

# ── Products menu ──────────────────────────────────────────────────────────────

async def products_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    products = await get_all_products()
    keyboard  = [[InlineKeyboardButton(f"🔐 {p['name']}", callback_data=f"product_{p['key']}")] for p in products]
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
    await query.edit_message_text("🛒 المنتجات المتاحة\n\nاختر المنتج:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_key = query.data.replace("product_", "")
    context.user_data['selected_product'] = product_key

    # Fetch product name, count, and price in parallel
    products_task      = get_all_products()
    count_task         = db_count("accounts", {"product_key": product_key, "sold": False})
    price_task         = get_product_price(product_key)
    products, count, price = await asyncio.gather(products_task, count_task, price_task)

    product_name = next((p['name'] for p in products if p['key'] == product_key), product_key)
    text = f"🔐 {product_name}\n\n📦 الكمية المتوفرة: {count}\n💵 السعر: {price:,} ل.س للحساب الواحد"

    if count > 0 and price > 0:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 شراء", callback_data=f"buy_{product_key}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="products_menu")],
        ])
    else:
        text += "\n\n⚠️ غير متوفر حالياً أو السعر غير محدد"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="products_menu")]])

    await query.edit_message_text(text, reply_markup=keyboard)

# ── Buy flow ───────────────────────────────────────────────────────────────────

async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.replace("buy_", "")
    context.user_data['buying_product'] = product_key
    await query.edit_message_text("🔢 كم عدد الحسابات المراد شراؤها؟ (أدخل الرقم فقط)")
    return BUY_QUANTITY

async def buy_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        quantity = int(update.message.text.strip())
        if quantity <= 0:
            await update.message.reply_text("❌ الرجاء إدخال عدد صحيح أكبر من صفر")
            return BUY_QUANTITY
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح فقط")
        return BUY_QUANTITY

    product_key = context.user_data.get('buying_product')
    user_id     = update.effective_user.id

    price, balance, available = await asyncio.gather(
        get_product_price(product_key),
        get_user_balance(user_id),
        db_count("accounts", {"product_key": product_key, "sold": False}),
    )
    total_price = price * quantity

    if balance < total_price:
        await update.message.reply_text(
            f"❌ رصيدك غير كافي!\n\n💵 رصيدك الحالي: {balance:,} ل.س\n💰 المطلوب: {total_price:,} ل.س",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    if available < quantity:
        await update.message.reply_text(
            f"❌ الكمية المطلوبة غير متوفرة!\n\n📦 المتوفر: {available} حساب",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    accounts = await db_find("accounts", {"product_key": product_key, "sold": False}, limit=quantity)

    # Mark sold + deduct balance in parallel
    now = datetime.now(timezone.utc).isoformat()
    mark_tasks = [
        db_update_one("accounts", {"_id": acc['_id']}, {
            "$set": {"sold": True, "sold_to": user_id, "sold_at": now}
        })
        for acc in accounts
    ]
    await asyncio.gather(*mark_tasks, update_user_balance(user_id, total_price, "subtract"))

    await db_insert_one("purchases", {
        "user_id":     user_id,
        "product_key": product_key,
        "quantity":    quantity,
        "total_price": total_price,
        "accounts":    [acc.get('account_data', '') for acc in accounts],
        "created_at":  now,
    })

    products     = await get_all_products()
    product_name = next((p['name'] for p in products if p['key'] == product_key), product_key)
    accounts_text = "\n".join([f"📧 {acc.get('account_data', 'N/A')}" for acc in accounts])

    await update.message.reply_text(
        f"✅ تم الشراء بنجاح!\n\n🔐 {product_name}\n📦 العدد: {quantity}\n💵 المبلغ: {total_price:,} ل.س\n\n📋 الحسابات:\n{accounts_text}",
        reply_markup=get_main_menu()
    )
    return ConversationHandler.END

# ── Admin: Add Accounts ────────────────────────────────────────────────────────

async def admin_add_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ غير مصرح لك", reply_markup=get_main_menu())
        return

    products = await get_all_products()
    keyboard  = [[InlineKeyboardButton(f"📝 {p['name']}", callback_data=f"addacc_{p['key']}")] for p in products]
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
    await query.edit_message_text(
        "➕ إضافة حسابات\n\nاختر المنتج لإضافة حسابات:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_select_product_for_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    product_key = query.data.replace("addacc_", "")
    context.user_data['admin_adding_to_product'] = product_key

    products     = await get_all_products()
    product_name = next((p['name'] for p in products if p['key'] == product_key), product_key)

    await query.edit_message_text(
        f"📝 إضافة حساب لـ: {product_name}\n\nأدخل بيانات الحساب (يمكنك إرسال عدة حسابات، كل حساب في سطر):"
    )
    return ADMIN_ADD_ACCOUNT

async def admin_account_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data['admin_account_data'] = update.message.text.strip()
    await update.message.reply_text("💵 أدخل سعر الحساب بالليرة السورية:")
    return ADMIN_ADD_ACCOUNT_PRICE

async def admin_account_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        price       = int(update.message.text.strip())
        product_key = context.user_data.get('admin_adding_to_product')
        accounts_raw = context.user_data.get('admin_account_data', '')

        # Support adding multiple accounts at once (one per line)
        lines = [l.strip() for l in accounts_raw.splitlines() if l.strip()]
        if not lines:
            await update.message.reply_text("❌ لم يتم إدخال أي حساب")
            return ConversationHandler.END

        now = datetime.now(timezone.utc).isoformat()
        insert_tasks = [
            db_insert_one("accounts", {
                "product_key":  product_key,
                "account_data": line,
                "price":        price,
                "sold":         False,
                "created_at":   now,
            })
            for line in lines
        ]
        results = await asyncio.gather(*insert_tasks)
        success = sum(1 for r in results if r is not None)

        # Update price setting
        await db_update_one("settings", {"key": f"price_{product_key}"}, {"$set": {"value": price}}, upsert=True)

        products     = await get_all_products()
        product_name = next((p['name'] for p in products if p['key'] == product_key), product_key)

        await update.message.reply_text(
            f"✅ تمت الإضافة!\n\n📦 المنتج: {product_name}\n💵 السعر: {price:,} ل.س\n➕ حسابات أُضيفت: {success} من {len(lines)}",
            reply_markup=get_admin_menu()
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_ADD_ACCOUNT_PRICE

# ── Admin: Add Product ─────────────────────────────────────────────────────────

async def admin_add_product_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("🆕 أدخل اسم المنتج الجديد:")
    return ADMIN_ADD_PRODUCT

async def admin_add_product_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    import re, random, string
    product_name = update.message.text.strip()
    product_key  = re.sub(r'[^a-zA-Z0-9_]', '', product_name.replace(" ", "_").lower())
    if not product_key:
        product_key = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    product_key = product_key[:32]

    existing = await db_find_one("products", {"key": product_key})
    if existing:
        await update.message.reply_text("❌ هذا المنتج موجود مسبقاً", reply_markup=get_admin_menu())
        return ConversationHandler.END

    now = datetime.now(timezone.utc).isoformat()
    await asyncio.gather(
        db_insert_one("products", {"name": product_name, "key": product_key, "created_at": now}),
        db_insert_one("settings", {"key": f"price_{product_key}", "value": 0}),
    )

    await update.message.reply_text(
        f"✅ تم إضافة المنتج بنجاح!\n\n📦 الاسم: {product_name}\n🔑 المفتاح: {product_key}",
        reply_markup=get_admin_menu()
    )
    return ConversationHandler.END

# ── Admin: Delete Product ──────────────────────────────────────────────────────

async def admin_delete_product_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    custom_products = await get_custom_products()
    if not custom_products:
        await query.edit_message_text(
            "❌ لا يوجد منتجات مخصصة للحذف\n\n⚠️ ملاحظة: لا يمكن حذف المنتجات الافتراضية",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")]])
        )
        return

    keyboard = [[InlineKeyboardButton(f"🗑️ {p['name']}", callback_data=f"delete_product_{p['key']}")] for p in custom_products]
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
    await query.edit_message_text(
        "🗑️ حذف منتج\n\n⚠️ اختر المنتج الذي تريد حذفه:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_confirm_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    product_key = query.data.replace("delete_product_", "")
    product     = await db_find_one("products", {"key": product_key})
    if not product:
        await query.edit_message_text("❌ لم يتم العثور على المنتج", reply_markup=get_admin_menu())
        return

    accounts_count = await db_count("accounts", {"product_key": product_key})
    await query.edit_message_text(
        f"⚠️ تأكيد الحذف\n\n📦 المنتج: {product['name']}\n📊 عدد الحسابات: {accounts_count}\n\n❓ هل أنت متأكد؟",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_delete_{product_key}"),
            InlineKeyboardButton("❌ إلغاء",    callback_data="admin_delete_product"),
        ]])
    )

async def admin_execute_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    product_key = query.data.replace("confirm_delete_", "")
    product     = await db_find_one("products", {"key": product_key})
    if not product:
        await query.edit_message_text("❌ لم يتم العثور على المنتج", reply_markup=get_admin_menu())
        return

    product_name = product['name']

    # Use helper functions (FIXED: was using db global directly which crashes if db is None)
    deleted_result = await db_delete_many("accounts",  {"product_key": product_key})
    deleted_count  = deleted_result.deleted_count if deleted_result else 0
    await asyncio.gather(
        db_delete_one("settings", {"key": f"price_{product_key}"}),
        db_delete_one("products", {"key": product_key}),
    )

    await query.edit_message_text(
        f"✅ تم حذف المنتج بنجاح!\n\n📦 المنتج: {product_name}\n🗑️ الحسابات المحذوفة: {deleted_count}",
        reply_markup=get_admin_menu()
    )

# ── Admin: Manual Balance ──────────────────────────────────────────────────────

async def admin_manual_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("👤 أدخل رقم ID المستخدم:")
    return ADMIN_MANUAL_BALANCE_USER

async def admin_manual_balance_user_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_MANUAL_BALANCE_USER

    user = await db_find_one("users", {"user_id": user_id})
    if not user:
        await update.message.reply_text("❌ لم يتم العثور على المستخدم", reply_markup=get_admin_menu())
        return ConversationHandler.END

    context.user_data['admin_balance_user_id'] = user_id
    await update.message.reply_text(f"💵 المستخدم: {user.get('first_name', 'N/A')}\n\nأدخل المبلغ (سيُضاف للرصيد):")
    return ADMIN_MANUAL_BALANCE_AMOUNT

async def admin_manual_balance_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        amount  = int(update.message.text.strip())
        user_id = context.user_data.get('admin_balance_user_id')
        await update_user_balance(user_id, amount)
        try:
            await context.bot.send_message(user_id, f"✅ تم إضافة {amount:,} ل.س إلى رصيدك من قبل الإدارة")
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ تم إضافة الرصيد!\n\n👤 ID: {user_id}\n💵 المبلغ: {amount:,} ل.س",
            reply_markup=get_admin_menu()
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_MANUAL_BALANCE_AMOUNT

# ── Admin: Broadcast ───────────────────────────────────────────────────────────

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await query.edit_message_text("📢 اكتب الرسالة التي تريد إرسالها لجميع المستخدمين:")
    return ADMIN_BROADCAST

async def admin_broadcast_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    message = update.message.text.strip()
    users   = await db_find("users", {}, limit=10000)
    success = fail = 0

    for user in users:
        try:
            await context.bot.send_message(user['user_id'], f"📢 رسالة من الإدارة:\n\n{message}")
            success += 1
            # Rate-limit: Telegram allows ~30 msgs/sec; sleep 40ms between each
            await asyncio.sleep(0.04)
        except Exception:
            fail += 1

    await update.message.reply_text(
        f"✅ تم الإرسال!\n\n✉️ نجح: {success}\n❌ فشل: {fail}",
        reply_markup=get_admin_menu()
    )
    return ConversationHandler.END

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    await query.edit_message_text("🔐 لوحة تحكم الأدمن\n\nاختر من الخيارات:", reply_markup=get_admin_menu())

# ── Recharge handlers ──────────────────────────────────────────────────────────

async def recharge_syriatel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💳 شحن الرصيد عبر سيريتل كاش\n\n📱 أرقام سيريتل كاش:\n0934595626\n0935579034\n\n📌 الخطوات:\n1️⃣ قم بتحويل المبلغ إلى أحد الأرقام\n2️⃣ اضغط تأكيد\n\n⚠️ احفظ رقم التحويلة (الكود)",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data="syriatel_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu"),
        ]])
    )

async def syriatel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['recharge_method'] = 'syriatel'
    await query.edit_message_text("💵 أدخل المبلغ الذي حولته بالليرة السورية:")
    return SYRIATEL_AMOUNT

async def syriatel_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            await update.message.reply_text("❌ أدخل مبلغ صحيح")
            return SYRIATEL_AMOUNT
        context.user_data['recharge_amount'] = amount
        await update.message.reply_text("🔢 أدخل رقم التحويل (الكود):")
        return SYRIATEL_CODE
    except ValueError:
        await update.message.reply_text("❌ أدخل رقم صحيح")
        return SYRIATEL_AMOUNT

async def syriatel_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code   = update.message.text.strip()
    amount = context.user_data.get('recharge_amount', 0)
    user   = update.effective_user

    result     = await db_insert_one("recharge_requests", {
        "user_id": user.id, "username": user.username, "first_name": user.first_name,
        "method": "syriatel", "amount": amount, "code": code, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    request_id = str(result.inserted_id) if result else "unknown"

    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📥 طلب شحن (سيريتل)\n\n👤 {user.first_name} (@{user.username})\n🆔 {user.id}\n💵 {amount:,} ل.س\n🔢 الكود: {code}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
                InlineKeyboardButton("❌ رفض",    callback_data=f"reject_recharge_{request_id}"),
            ]])
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

    await update.message.reply_text("✅ تم إرسال طلب الشحن!\n\nسيتم مراجعته قريباً.", reply_markup=get_main_menu())
    return ConversationHandler.END

async def recharge_shamcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💳 شحن الرصيد عبر شام كاش\n\n🔗 معرف شام كاش:\nbc9d9b41336308e2a4f9e0ffe86f48a0\n\n📌 حول المبلغ ثم اضغط تأكيد",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data="shamcash_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu"),
        ]])
    )

async def shamcash_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['recharge_method'] = 'shamcash'
    await query.edit_message_text("💵 كم المبلغ المراد شحنه بالليرة السورية:")
    return SHAMCASH_AMOUNT

async def shamcash_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            await update.message.reply_text("❌ أدخل مبلغ صحيح")
            return SHAMCASH_AMOUNT
        context.user_data['recharge_amount'] = amount
        await update.message.reply_text("📸 أرسل إثبات الدفع (صورة):")
        return SHAMCASH_PROOF
    except ValueError:
        await update.message.reply_text("❌ أدخل رقم صحيح")
        return SHAMCASH_AMOUNT

async def shamcash_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ أرسل صورة فقط")
        return SHAMCASH_PROOF

    photo  = update.message.photo[-1]
    amount = context.user_data.get('recharge_amount', 0)
    user   = update.effective_user

    result     = await db_insert_one("recharge_requests", {
        "user_id": user.id, "username": user.username, "first_name": user.first_name,
        "method": "shamcash", "amount": amount, "photo_file_id": photo.file_id, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    request_id = str(result.inserted_id) if result else "unknown"

    try:
        await context.bot.send_photo(
            ADMIN_ID, photo.file_id,
            caption=f"📥 طلب شحن (شام كاش)\n\n👤 {user.first_name} (@{user.username})\n🆔 {user.id}\n💵 {amount:,} ل.س",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
                InlineKeyboardButton("❌ رفض",    callback_data=f"reject_recharge_{request_id}"),
            ]])
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

    await update.message.reply_text("✅ تم إرسال طلب الشحن!", reply_markup=get_main_menu())
    return ConversationHandler.END

async def recharge_cwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rate = await get_exchange_rate()
    await query.edit_message_text(
        f"💳 شحن عبر cwallet\n\nمعرف المحفظة:\nTHEaoFQmETNbxiouYCBhKkaYZT4Zoo8GwJ\n\n1$ = {rate} ل.س",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data="cwallet_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu"),
        ]])
    )

async def cwallet_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['recharge_method'] = 'cwallet'
    await query.edit_message_text("💵 أدخل المبلغ بالليرة السورية:")
    return CWALLET_AMOUNT

async def cwallet_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            await update.message.reply_text("❌ أدخل مبلغ صحيح")
            return CWALLET_AMOUNT
        context.user_data['recharge_amount'] = amount
        await update.message.reply_text("📸 أرسل إثبات الدفع (صورة):")
        return CWALLET_PROOF
    except ValueError:
        await update.message.reply_text("❌ أدخل رقم صحيح")
        return CWALLET_AMOUNT

async def cwallet_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ أرسل صورة فقط")
        return CWALLET_PROOF

    photo  = update.message.photo[-1]
    amount = context.user_data.get('recharge_amount', 0)
    user   = update.effective_user

    result     = await db_insert_one("recharge_requests", {
        "user_id": user.id, "username": user.username, "first_name": user.first_name,
        "method": "cwallet", "amount": amount, "photo_file_id": photo.file_id, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    request_id = str(result.inserted_id) if result else "unknown"

    try:
        await context.bot.send_photo(
            ADMIN_ID, photo.file_id,
            caption=f"📥 طلب شحن (cwallet)\n\n👤 {user.first_name}\n🆔 {user.id}\n💵 {amount:,} ل.س",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
                InlineKeyboardButton("❌ رفض",    callback_data=f"reject_recharge_{request_id}"),
            ]])
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

    await update.message.reply_text("✅ تم إرسال طلب الشحن!", reply_markup=get_main_menu())
    return ConversationHandler.END

async def recharge_coinex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rate = await get_exchange_rate()
    await query.edit_message_text(
        f"💳 شحن عبر coinex\n\nمعرف المحفظة:\n0xaace6d4956b27c293018556bedba49a5074d6020\n\nأو الإيميل:\nkm197807@gmail.com\n\n1$ = {rate} ل.س",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data="coinex_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu"),
        ]])
    )

async def coinex_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['recharge_method'] = 'coinex'
    await query.edit_message_text("💵 أدخل المبلغ بالليرة السورية:")
    return COINEX_AMOUNT

async def coinex_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            await update.message.reply_text("❌ أدخل مبلغ صحيح")
            return COINEX_AMOUNT
        context.user_data['recharge_amount'] = amount
        await update.message.reply_text("📸 أرسل إثبات الدفع (صورة):")
        return COINEX_PROOF
    except ValueError:
        await update.message.reply_text("❌ أدخل رقم صحيح")
        return COINEX_AMOUNT

async def coinex_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ أرسل صورة فقط")
        return COINEX_PROOF

    photo  = update.message.photo[-1]
    amount = context.user_data.get('recharge_amount', 0)
    user   = update.effective_user

    result     = await db_insert_one("recharge_requests", {
        "user_id": user.id, "username": user.username, "first_name": user.first_name,
        "method": "coinex", "amount": amount, "photo_file_id": photo.file_id, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    request_id = str(result.inserted_id) if result else "unknown"

    try:
        await context.bot.send_photo(
            ADMIN_ID, photo.file_id,
            caption=f"📥 طلب شحن (coinex)\n\n👤 {user.first_name}\n🆔 {user.id}\n💵 {amount:,} ل.س",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
                InlineKeyboardButton("❌ رفض",    callback_data=f"reject_recharge_{request_id}"),
            ]])
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

    await update.message.reply_text("✅ تم إرسال طلب الشحن!", reply_markup=get_main_menu())
    return ConversationHandler.END

# ── Recharge approval ──────────────────────────────────────────────────────────

def _extract_request_id(callback_data: str, prefix: str) -> str:
    """Safely extract the MongoDB ObjectId from callback_data."""
    # callback_data looks like: "approve_recharge_<24-char-hex>"
    # prefix looks like: "approve_recharge_"
    return callback_data[len(prefix):]

async def approve_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    request_id = _extract_request_id(query.data, "approve_recharge_")
    try:
        request = await db_find_one("recharge_requests", {"_id": ObjectId(request_id)})
    except Exception as e:
        logger.error(f"approve_recharge: bad ObjectId {request_id}: {e}")
        await query.edit_message_text("❌ معرّف الطلب غير صالح")
        return

    if not request:
        await query.edit_message_text("❌ لم يتم العثور على الطلب")
        return
    if request.get("status") != "pending":
        await query.edit_message_text("❌ تم معالجة هذا الطلب مسبقاً")
        return

    await asyncio.gather(
        db_update_one("recharge_requests", {"_id": ObjectId(request_id)}, {
            "$set": {"status": "approved", "approved_at": datetime.now(timezone.utc).isoformat()}
        }),
        update_user_balance(request["user_id"], request["amount"]),
    )

    try:
        await context.bot.send_message(
            request["user_id"],
            f"✅ تم الموافقة على طلب الشحن!\n\n💵 تم إضافة {request['amount']:,} ل.س إلى رصيدك",
            reply_markup=get_main_menu()
        )
    except Exception:
        pass

    await query.edit_message_text(
        f"✅ تمت الموافقة\n\n💵 {request['amount']:,} ل.س\n👤 {request.get('first_name', 'N/A')}"
    )

async def reject_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    request_id = _extract_request_id(query.data, "reject_recharge_")
    try:
        request = await db_find_one("recharge_requests", {"_id": ObjectId(request_id)})
    except Exception as e:
        logger.error(f"reject_recharge: bad ObjectId {request_id}: {e}")
        await query.edit_message_text("❌ معرّف الطلب غير صالح")
        return

    if not request:
        await query.edit_message_text("❌ لم يتم العثور على الطلب")
        return

    await db_update_one("recharge_requests", {"_id": ObjectId(request_id)}, {
        "$set": {"status": "rejected", "rejected_at": datetime.now(timezone.utc).isoformat()}
    })

    try:
        await context.bot.send_message(request["user_id"], "❌ تم رفض طلب الشحن", reply_markup=get_main_menu())
    except Exception:
        pass

    await query.edit_message_text(f"❌ تم الرفض\n\n👤 {request.get('first_name', 'N/A')}")

# ── Cancel ─────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء", reply_markup=get_main_menu())
    return ConversationHandler.END

# ── DB initialisation ──────────────────────────────────────────────────────────

async def init_database():
    if db is None:
        return
    try:
        if not await db_find_one("settings", {"key": "exchange_rate"}):
            await db_insert_one("settings", {"key": "exchange_rate", "value": 120})
        tasks = []
        for product in DEFAULT_PRODUCTS:
            key = f"price_{product['key']}"
            if not await db_find_one("settings", {"key": key}):
                tasks.append(db_insert_one("settings", {"key": key, "value": 0}))
        if tasks:
            await asyncio.gather(*tasks)
        logger.info("Database initialised")
    except Exception as e:
        logger.error(f"Database init error: {e}")

async def post_init(application: Application):
    await init_database()

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not set. Please add it as a Replit Secret.")
        return
    if ADMIN_ID == 0:
        logger.warning("ADMIN_ID is not set or is 0 – admin commands will not work.")
    if not MONGO_URL:
        logger.warning("MONGO_URL is not set – all database operations will be disabled.")

    # Blocking pre-check for MongoDB (run in new event loop before Application starts)
    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    loop.run_until_complete(init_mongo())
    loop.close()

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(10)
        .read_timeout(10)
        .write_timeout(10)
        .pool_timeout(10)
        .post_init(post_init)
        .build()
    )

    # ── Conversation handlers ──────────────────────────────────────────────────
    syriatel_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(syriatel_confirm, pattern="^syriatel_confirm$")],
        states={
            SYRIATEL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, syriatel_amount_received)],
            SYRIATEL_CODE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, syriatel_code_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    shamcash_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(shamcash_confirm, pattern="^shamcash_confirm$")],
        states={
            SHAMCASH_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, shamcash_amount_received)],
            SHAMCASH_PROOF:  [MessageHandler(filters.PHOTO, shamcash_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    cwallet_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(cwallet_confirm, pattern="^cwallet_confirm$")],
        states={
            CWALLET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cwallet_amount_received)],
            CWALLET_PROOF:  [MessageHandler(filters.PHOTO, cwallet_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    coinex_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(coinex_confirm, pattern="^coinex_confirm$")],
        states={
            COINEX_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, coinex_amount_received)],
            COINEX_PROOF:  [MessageHandler(filters.PHOTO, coinex_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    buy_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_product, pattern="^buy_")],
        states={
            BUY_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_quantity_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    admin_add_account_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_select_product_for_account, pattern="^addacc_")],
        states={
            ADMIN_ADD_ACCOUNT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_account_received)],
            ADMIN_ADD_ACCOUNT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_account_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_back, pattern="^admin_back$"),
        ],
        allow_reentry=True,
    )

    admin_broadcast_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast, pattern="^admin_broadcast$")],
        states={
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    admin_add_product_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_product_menu, pattern="^admin_add_product$")],
        states={
            ADMIN_ADD_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_product_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    admin_manual_balance_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_manual_balance, pattern="^admin_manual_balance$")],
        states={
            ADMIN_MANUAL_BALANCE_USER:   [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manual_balance_user_received)],
            ADMIN_MANUAL_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manual_balance_amount_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))

    # Conversation handlers FIRST
    application.add_handler(syriatel_handler)
    application.add_handler(shamcash_handler)
    application.add_handler(cwallet_handler)
    application.add_handler(coinex_handler)
    application.add_handler(buy_handler)
    application.add_handler(admin_add_account_handler)
    application.add_handler(admin_broadcast_handler)
    application.add_handler(admin_add_product_handler)
    application.add_handler(admin_manual_balance_handler)

    # Regular callback handlers
    application.add_handler(CallbackQueryHandler(main_menu,     pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(balance_menu,  pattern="^balance_menu$"))
    application.add_handler(CallbackQueryHandler(my_balance,    pattern="^my_balance$"))
    application.add_handler(CallbackQueryHandler(recharge_menu, pattern="^recharge_menu$"))
    application.add_handler(CallbackQueryHandler(support,       pattern="^support$"))

    application.add_handler(CallbackQueryHandler(products_menu, pattern="^products_menu$"))
    application.add_handler(CallbackQueryHandler(show_product,  pattern="^product_"))

    application.add_handler(CallbackQueryHandler(recharge_syriatel, pattern="^recharge_syriatel$"))
    application.add_handler(CallbackQueryHandler(recharge_shamcash, pattern="^recharge_shamcash$"))
    application.add_handler(CallbackQueryHandler(recharge_cwallet,  pattern="^recharge_cwallet$"))
    application.add_handler(CallbackQueryHandler(recharge_coinex,   pattern="^recharge_coinex$"))
    application.add_handler(CallbackQueryHandler(approve_recharge,  pattern="^approve_recharge_"))
    application.add_handler(CallbackQueryHandler(reject_recharge,   pattern="^reject_recharge_"))

    application.add_handler(CallbackQueryHandler(admin_add_accounts,          pattern="^admin_add_accounts$"))
    application.add_handler(CallbackQueryHandler(admin_delete_product_menu,   pattern="^admin_delete_product$"))
    application.add_handler(CallbackQueryHandler(admin_confirm_delete_product, pattern="^delete_product_"))
    application.add_handler(CallbackQueryHandler(admin_execute_delete_product, pattern="^confirm_delete_"))
    application.add_handler(CallbackQueryHandler(admin_back,                  pattern="^admin_back$"))

    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
