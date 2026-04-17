"""
Telegram Bot for Account Sales - Professional Version
"""
import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
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

# Load environment variables
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configuration
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME', 'telegram_bot')

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# MongoDB connection
try:
    mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    logger.info("MongoDB connection initialized")
except Exception as e:
    logger.error(f"MongoDB connection error: {e}")
    db = None

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
) = range(15)

# ==================== Products Configuration ====================
# المنتجات الافتراضية - هذه ثابتة ولا تحتاج قاعدة بيانات
DEFAULT_PRODUCTS = [
    {"name": "حسابات icloud", "key": "icloud"},
    {"name": "حسابات gmail", "key": "gmail"},
    {"name": "حسابات outlook", "key": "outlook"},
    {"name": "حسابات paypal", "key": "paypal"},
]

def get_default_products():
    """Get default products - always works without DB"""
    return DEFAULT_PRODUCTS.copy()

async def get_custom_products():
    """Get custom products from database"""
    try:
        if db is None:
            return []
        custom = await db.products.find({"key": {"$ne": None, "$exists": True}}).to_list(100)
        return [{"name": c["name"], "key": c["key"]} for c in custom if c.get("key")]
    except Exception as e:
        logger.error(f"Error fetching custom products: {e}")
        return []

async def get_all_products():
    """Get all products - default + custom"""
    products = get_default_products()
    custom = await get_custom_products()
    products.extend(custom)
    return products

# ==================== Database Helpers ====================

async def db_find_one(collection, query):
    """Safe database find_one"""
    try:
        if db is None:
            return None
        return await db[collection].find_one(query)
    except Exception as e:
        logger.error(f"DB find_one error ({collection}): {e}")
        return None

async def db_insert_one(collection, document):
    """Safe database insert_one"""
    try:
        if db is None:
            return None
        return await db[collection].insert_one(document)
    except Exception as e:
        logger.error(f"DB insert_one error ({collection}): {e}")
        return None

async def db_update_one(collection, query, update, upsert=False):
    """Safe database update_one"""
    try:
        if db is None:
            return None
        return await db[collection].update_one(query, update, upsert=upsert)
    except Exception as e:
        logger.error(f"DB update_one error ({collection}): {e}")
        return None

async def db_count(collection, query):
    """Safe database count"""
    try:
        if db is None:
            return 0
        return await db[collection].count_documents(query)
    except Exception as e:
        logger.error(f"DB count error ({collection}): {e}")
        return 0

async def db_find(collection, query, limit=100):
    """Safe database find"""
    try:
        if db is None:
            return []
        return await db[collection].find(query).to_list(limit)
    except Exception as e:
        logger.error(f"DB find error ({collection}): {e}")
        return []

# ==================== User Functions ====================

async def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    """Get or create user"""
    user = await db_find_one("users", {"user_id": user_id})
    if not user:
        user = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "balance": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db_insert_one("users", user)
    return user

async def get_user_balance(user_id: int) -> int:
    """Get user balance"""
    user = await db_find_one("users", {"user_id": user_id})
    return user.get("balance", 0) if user else 0

async def update_user_balance(user_id: int, amount: int, operation: str = "add"):
    """Update user balance"""
    if operation == "add":
        await db_update_one("users", {"user_id": user_id}, {"$inc": {"balance": amount}})
    else:
        await db_update_one("users", {"user_id": user_id}, {"$inc": {"balance": -amount}})

async def get_exchange_rate() -> int:
    """Get exchange rate"""
    settings = await db_find_one("settings", {"key": "exchange_rate"})
    return settings.get("value", 120) if settings else 120

async def get_product_price(product_key: str) -> int:
    """Get product price"""
    settings = await db_find_one("settings", {"key": f"price_{product_key}"})
    return settings.get("value", 0) if settings else 0

# ==================== Keyboards ====================

def get_main_menu():
    """Main menu keyboard"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 الرصيد", callback_data="balance_menu")],
        [InlineKeyboardButton("🛒 المنتجات", callback_data="products_menu")],
        [InlineKeyboardButton("📞 تواصل مع الدعم", callback_data="support")],
    ])

def get_admin_menu():
    """Admin menu keyboard"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة حسابات", callback_data="admin_add_accounts")],
        [InlineKeyboardButton("📢 إرسال رسالة عامة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🆕 إضافة منتجات", callback_data="admin_add_product")],
        [InlineKeyboardButton("🗑️ حذف منتج", callback_data="admin_delete_product")],
        [InlineKeyboardButton("💰 إضافة رصيد يدوي", callback_data="admin_manual_balance")],
    ])

# ==================== Command Handlers ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user = update.effective_user
    await get_or_create_user(user.id, user.username, user.first_name)
    
    await update.message.reply_text(
        f"مرحباً {user.first_name}! 👋\n\nأهلاً بك في بوت بيع الحسابات\n\nاختر من القائمة أدناه:",
        reply_markup=get_main_menu()
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ غير مصرح لك بالوصول")
        return
    
    await update.message.reply_text(
        "🔐 لوحة تحكم الأدمن\n\nاختر من الخيارات:",
        reply_markup=get_admin_menu()
    )

# ==================== Menu Handlers ====================

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    
    await query.edit_message_text(
        f"مرحباً {user.first_name}! 👋\n\nأهلاً بك في بوت بيع الحسابات\n\nاختر من القائمة أدناه:",
        reply_markup=get_main_menu()
    )

async def balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Balance menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 شحن الرصيد", callback_data="recharge_menu")],
        [InlineKeyboardButton("💵 رصيدي", callback_data="my_balance")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")],
    ])
    
    await query.edit_message_text("💰 قائمة الرصيد\n\nاختر من الخيارات:", reply_markup=keyboard)

async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show balance"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    balance = await get_user_balance(user_id)
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="balance_menu")]])
    await query.edit_message_text(
        f"💵 رصيدي\n\n💰 الرصيد الحالي: {balance:,} ل.س\n🆔 معرف التلجرام: {user_id}",
        reply_markup=keyboard
    )

async def recharge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recharge menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 سيريتل كاش", callback_data="recharge_syriatel")],
        [InlineKeyboardButton("💳 شام كاش", callback_data="recharge_shamcash")],
        [InlineKeyboardButton("🔷 محفظة cwallet", callback_data="recharge_cwallet")],
        [InlineKeyboardButton("🟠 محفظة coinex", callback_data="recharge_coinex")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="balance_menu")],
    ])
    
    await query.edit_message_text("💳 شحن الرصيد\n\nاختر طريقة الشحن:", reply_markup=keyboard)

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support"""
    query = update.callback_query
    await query.answer()
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]])
    await query.edit_message_text(
        "📞 تواصل مع الدعم\n\nللتواصل مع الدعم اضغط على معرف الأدمن:\n@km0997055",
        reply_markup=keyboard
    )

# ==================== Products Menu ====================

async def products_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Products menu - عرض المنتجات"""
    query = update.callback_query
    await query.answer()
    
    logger.info("products_menu called")
    
    # جلب المنتجات
    products = await get_all_products()
    logger.info(f"Products fetched: {len(products)}")
    
    # بناء الكيبورد
    keyboard = []
    for product in products:
        keyboard.append([InlineKeyboardButton(f"🔐 {product['name']}", callback_data=f"product_{product['key']}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
    
    await query.edit_message_text(
        "🛒 المنتجات المتاحة\n\nاختر المنتج:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show product details"""
    query = update.callback_query
    await query.answer()
    
    product_key = query.data.replace("product_", "")
    context.user_data['selected_product'] = product_key
    
    # Get product name
    products = await get_all_products()
    product_name = next((p['name'] for p in products if p['key'] == product_key), product_key)
    
    # Get accounts count and price
    accounts_count = await db_count("accounts", {"product_key": product_key, "sold": False})
    price = await get_product_price(product_key)
    
    text = f"🔐 {product_name}\n\n📦 الكمية المتوفرة: {accounts_count}\n💵 السعر: {price:,} ل.س للحساب الواحد"
    
    if accounts_count > 0 and price > 0:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 شراء", callback_data=f"buy_{product_key}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="products_menu")],
        ])
    else:
        text += "\n\n⚠️ غير متوفر حالياً أو السعر غير محدد"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="products_menu")]])
    
    await query.edit_message_text(text, reply_markup=keyboard)

# ==================== Buy Product ====================

async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start buy process"""
    query = update.callback_query
    await query.answer()
    
    product_key = query.data.replace("buy_", "")
    context.user_data['buying_product'] = product_key
    
    await query.edit_message_text("🔢 كم عدد الحسابات المراد شراؤها؟ (أدخل الرقم فقط)")
    return BUY_QUANTITY

async def buy_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive buy quantity"""
    try:
        quantity = int(update.message.text.strip())
        if quantity <= 0:
            await update.message.reply_text("❌ الرجاء إدخال عدد صحيح أكبر من صفر")
            return BUY_QUANTITY
        
        product_key = context.user_data.get('buying_product')
        user_id = update.effective_user.id
        
        # Get price and check balance
        price = await get_product_price(product_key)
        total_price = price * quantity
        balance = await get_user_balance(user_id)
        
        if balance < total_price:
            await update.message.reply_text(
                f"❌ رصيدك غير كافي!\n\n💵 رصيدك الحالي: {balance:,} ل.س\n💰 المطلوب: {total_price:,} ل.س",
                reply_markup=get_main_menu()
            )
            return ConversationHandler.END
        
        # Check available accounts
        available = await db_count("accounts", {"product_key": product_key, "sold": False})
        if available < quantity:
            await update.message.reply_text(
                f"❌ الكمية المطلوبة غير متوفرة!\n\n📦 المتوفر: {available} حساب",
                reply_markup=get_main_menu()
            )
            return ConversationHandler.END
        
        # Get accounts
        accounts = await db_find("accounts", {"product_key": product_key, "sold": False}, limit=quantity)
        
        # Mark as sold
        for acc in accounts:
            await db_update_one("accounts", {"_id": acc['_id']}, {
                "$set": {"sold": True, "sold_to": user_id, "sold_at": datetime.now(timezone.utc).isoformat()}
            })
        
        # Deduct balance
        await update_user_balance(user_id, total_price, "subtract")
        
        # Save purchase
        await db_insert_one("purchases", {
            "user_id": user_id,
            "product_key": product_key,
            "quantity": quantity,
            "total_price": total_price,
            "accounts": [acc.get('account_data', '') for acc in accounts],
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        
        # Get product name
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

# ==================== Admin: Add Accounts ====================

async def admin_add_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Show products to add accounts"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    logger.info("admin_add_accounts called")
    
    # جلب المنتجات
    products = await get_all_products()
    logger.info(f"Admin products fetched: {len(products)}")
    
    keyboard = []
    for product in products:
        keyboard.append([InlineKeyboardButton(f"📝 {product['name']}", callback_data=f"addacc_{product['key']}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
    
    await query.edit_message_text(
        "➕ إضافة حسابات\n\nاختر المنتج لإضافة حسابات:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_select_product_for_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Select product to add account"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    product_key = query.data.replace("addacc_", "")
    context.user_data['admin_adding_to_product'] = product_key
    
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
    """Admin: Receive price and save"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    try:
        price = int(update.message.text.strip())
        product_key = context.user_data.get('admin_adding_to_product')
        account_data = context.user_data.get('admin_account_data')
        
        # Save account
        await db_insert_one("accounts", {
            "product_key": product_key,
            "account_data": account_data,
            "price": price,
            "sold": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        
        # Update price
        await db_update_one("settings", {"key": f"price_{product_key}"}, {"$set": {"value": price}}, upsert=True)
        
        await update.message.reply_text(
            f"✅ تم إضافة الحساب بنجاح!\n\n📦 المنتج: {product_key}\n💵 السعر: {price:,} ل.س",
            reply_markup=get_admin_menu()
        )
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_ADD_ACCOUNT_PRICE

# ==================== Admin: Add Product ====================

async def admin_add_product_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Add product menu"""
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
    
    # Check if exists
    existing = await db_find_one("products", {"key": product_key})
    if existing:
        await update.message.reply_text("❌ هذا المنتج موجود مسبقاً", reply_markup=get_admin_menu())
        return ConversationHandler.END
    
    # Add product
    await db_insert_one("products", {
        "name": product_name,
        "key": product_key,
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    
    # Initialize price
    await db_insert_one("settings", {"key": f"price_{product_key}", "value": 0})
    
    await update.message.reply_text(f"✅ تم إضافة المنتج بنجاح!\n\n📦 الاسم: {product_name}", reply_markup=get_admin_menu())
    return ConversationHandler.END

# ==================== Admin: Delete Product ====================

async def admin_delete_product_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Delete product menu"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    # Get custom products only
    custom_products = await get_custom_products()
    
    if not custom_products:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")]])
        await query.edit_message_text(
            "❌ لا يوجد منتجات مخصصة للحذف\n\n⚠️ ملاحظة: لا يمكن حذف المنتجات الافتراضية",
            reply_markup=keyboard
        )
        return
    
    keyboard = []
    for product in custom_products:
        keyboard.append([InlineKeyboardButton(f"🗑️ {product['name']}", callback_data=f"delete_product_{product['key']}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
    
    await query.edit_message_text(
        "🗑️ حذف منتج\n\n⚠️ اختر المنتج الذي تريد حذفه:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_confirm_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Confirm delete"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    product_key = query.data.replace("delete_product_", "")
    
    product = await db_find_one("products", {"key": product_key})
    if not product:
        await query.edit_message_text("❌ لم يتم العثور على المنتج", reply_markup=get_admin_menu())
        return
    
    accounts_count = await db_count("accounts", {"product_key": product_key})
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_delete_{product_key}"),
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_delete_product")
        ]
    ])
    
    await query.edit_message_text(
        f"⚠️ تأكيد الحذف\n\n📦 المنتج: {product['name']}\n📊 عدد الحسابات: {accounts_count}\n\n❓ هل أنت متأكد؟",
        reply_markup=keyboard
    )

async def admin_execute_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Execute delete"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    product_key = query.data.replace("confirm_delete_", "")
    
    product = await db_find_one("products", {"key": product_key})
    if not product:
        await query.edit_message_text("❌ لم يتم العثور على المنتج", reply_markup=get_admin_menu())
        return
    
    product_name = product['name']
    
    # Delete accounts
    try:
        deleted = await db["accounts"].delete_many({"product_key": product_key})
        deleted_count = deleted.deleted_count
    except:
        deleted_count = 0
    
    # Delete settings
    try:
        await db["settings"].delete_one({"key": f"price_{product_key}"})
    except:
        pass
    
    # Delete product
    try:
        await db["products"].delete_one({"key": product_key})
    except:
        pass
    
    await query.edit_message_text(
        f"✅ تم حذف المنتج بنجاح!\n\n📦 المنتج: {product_name}\n🗑️ الحسابات المحذوفة: {deleted_count}",
        reply_markup=get_admin_menu()
    )

# ==================== Admin: Manual Balance ====================

async def admin_manual_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Manual balance"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.edit_message_text("👤 أدخل رقم ID المستخدم:")
    return ADMIN_MANUAL_BALANCE_USER

async def admin_manual_balance_user_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Receive user ID"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    try:
        user_id = int(update.message.text.strip())
        user = await db_find_one("users", {"user_id": user_id})
        
        if not user:
            await update.message.reply_text("❌ لم يتم العثور على المستخدم", reply_markup=get_admin_menu())
            return ConversationHandler.END
        
        context.user_data['admin_balance_user_id'] = user_id
        await update.message.reply_text(f"💵 المستخدم: {user.get('first_name', 'N/A')}\n\nأدخل المبلغ:")
        return ADMIN_MANUAL_BALANCE_AMOUNT
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_MANUAL_BALANCE_USER

async def admin_manual_balance_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Add balance"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    try:
        amount = int(update.message.text.strip())
        user_id = context.user_data.get('admin_balance_user_id')
        
        await update_user_balance(user_id, amount)
        
        try:
            await context.bot.send_message(user_id, f"✅ تم إضافة {amount:,} ل.س إلى رصيدك من قبل الإدارة")
        except:
            pass
        
        await update.message.reply_text(
            f"✅ تم إضافة الرصيد!\n\n👤 ID: {user_id}\n💵 المبلغ: {amount:,} ل.س",
            reply_markup=get_admin_menu()
        )
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        return ADMIN_MANUAL_BALANCE_AMOUNT

# ==================== Admin: Broadcast ====================

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Broadcast"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.edit_message_text("📢 اكتب الرسالة:")
    return ADMIN_BROADCAST

async def admin_broadcast_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Send broadcast"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    message = update.message.text.strip()
    users = await db_find("users", {}, limit=10000)
    
    success = 0
    fail = 0
    
    for user in users:
        try:
            await context.bot.send_message(user['user_id'], f"📢 رسالة من الإدارة:\n\n{message}")
            success += 1
        except:
            fail += 1
    
    await update.message.reply_text(f"✅ تم الإرسال!\n\n✉️ نجح: {success}\n❌ فشل: {fail}", reply_markup=get_admin_menu())
    return ConversationHandler.END

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Back"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.edit_message_text("🔐 لوحة تحكم الأدمن\n\nاختر من الخيارات:", reply_markup=get_admin_menu())

# ==================== Recharge Handlers ====================

async def recharge_syriatel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = """💳 شحن الرصيد عبر سيريتل كاش

📱 أرقام سيريتل كاش:
0934595626
0935579034

📌 الخطوات:
1️⃣ قم بتحويل المبلغ إلى أحد الأرقام
2️⃣ اضغط تأكيد

⚠️ احفظ رقم التحويلة (الكود)"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="syriatel_confirm"), InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard)

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
    code = update.message.text.strip()
    amount = context.user_data.get('recharge_amount', 0)
    user = update.effective_user
    
    request = {
        "user_id": user.id, "username": user.username, "first_name": user.first_name,
        "method": "syriatel", "amount": amount, "code": code, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    result = await db_insert_one("recharge_requests", request)
    request_id = str(result.inserted_id) if result else "unknown"
    
    admin_text = f"📥 طلب شحن (سيريتل)\n\n👤 {user.first_name} (@{user.username})\n🆔 {user.id}\n💵 {amount:,} ل.س\n🔢 {code}"
    admin_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_recharge_{request_id}")
    ]])
    
    try:
        await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_keyboard)
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")
    
    await update.message.reply_text("✅ تم إرسال طلب الشحن!\n\nسيتم مراجعته قريباً.", reply_markup=get_main_menu())
    return ConversationHandler.END

# Similar handlers for shamcash, cwallet, coinex...

async def recharge_shamcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = """💳 شحن الرصيد عبر شام كاش

🔗 معرف شام كاش:
bc9d9b41336308e2a4f9e0ffe86f48a0

📌 حول المبلغ ثم اضغط تأكيد"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="shamcash_confirm"), InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard)

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
    
    photo = update.message.photo[-1]
    amount = context.user_data.get('recharge_amount', 0)
    user = update.effective_user
    
    request = {
        "user_id": user.id, "username": user.username, "first_name": user.first_name,
        "method": "shamcash", "amount": amount, "photo_file_id": photo.file_id, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    result = await db_insert_one("recharge_requests", request)
    request_id = str(result.inserted_id) if result else "unknown"
    
    admin_text = f"📥 طلب شحن (شام كاش)\n\n👤 {user.first_name} (@{user.username})\n🆔 {user.id}\n💵 {amount:,} ل.س"
    admin_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_recharge_{request_id}")
    ]])
    
    try:
        await context.bot.send_photo(ADMIN_ID, photo.file_id, caption=admin_text, reply_markup=admin_keyboard)
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")
    
    await update.message.reply_text("✅ تم إرسال طلب الشحن!", reply_markup=get_main_menu())
    return ConversationHandler.END

async def recharge_cwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rate = await get_exchange_rate()
    
    text = f"""💳 شحن عبر cwallet

معرف المحفظة:
THEaoFQmETNbxiouYCBhKkaYZT4Zoo8GwJ

1$={rate} ل.س"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="cwallet_confirm"), InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard)

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
    
    photo = update.message.photo[-1]
    amount = context.user_data.get('recharge_amount', 0)
    user = update.effective_user
    
    request = {
        "user_id": user.id, "username": user.username, "first_name": user.first_name,
        "method": "cwallet", "amount": amount, "photo_file_id": photo.file_id, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    result = await db_insert_one("recharge_requests", request)
    request_id = str(result.inserted_id) if result else "unknown"
    
    admin_text = f"📥 طلب شحن (cwallet)\n\n👤 {user.first_name}\n🆔 {user.id}\n💵 {amount:,} ل.س"
    admin_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_recharge_{request_id}")
    ]])
    
    try:
        await context.bot.send_photo(ADMIN_ID, photo.file_id, caption=admin_text, reply_markup=admin_keyboard)
    except:
        pass
    
    await update.message.reply_text("✅ تم إرسال طلب الشحن!", reply_markup=get_main_menu())
    return ConversationHandler.END

async def recharge_coinex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rate = await get_exchange_rate()
    
    text = f"""💳 شحن عبر coinex

معرف المحفظة:
0xaace6d4956b27c293018556bedba49a5074d6020

أو الإيميل:
km197807@gmail.com

1$={rate} ل.س"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="coinex_confirm"), InlineKeyboardButton("❌ إلغاء", callback_data="recharge_menu")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard)

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
    
    photo = update.message.photo[-1]
    amount = context.user_data.get('recharge_amount', 0)
    user = update.effective_user
    
    request = {
        "user_id": user.id, "username": user.username, "first_name": user.first_name,
        "method": "coinex", "amount": amount, "photo_file_id": photo.file_id, "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    result = await db_insert_one("recharge_requests", request)
    request_id = str(result.inserted_id) if result else "unknown"
    
    admin_text = f"📥 طلب شحن (coinex)\n\n👤 {user.first_name}\n🆔 {user.id}\n💵 {amount:,} ل.س"
    admin_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ موافقة", callback_data=f"approve_recharge_{request_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_recharge_{request_id}")
    ]])
    
    try:
        await context.bot.send_photo(ADMIN_ID, photo.file_id, caption=admin_text, reply_markup=admin_keyboard)
    except:
        pass
    
    await update.message.reply_text("✅ تم إرسال طلب الشحن!", reply_markup=get_main_menu())
    return ConversationHandler.END

# ==================== Recharge Approval ====================

async def approve_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    request_id = query.data.split("_")[-1]
    
    try:
        request = await db_find_one("recharge_requests", {"_id": ObjectId(request_id)})
    except:
        await query.edit_message_text("❌ لم يتم العثور على الطلب")
        return
    
    if not request:
        await query.edit_message_text("❌ لم يتم العثور على الطلب")
        return
    
    if request.get("status") != "pending":
        await query.edit_message_text("❌ تم معالجة هذا الطلب مسبقاً")
        return
    
    await db_update_one("recharge_requests", {"_id": ObjectId(request_id)}, {
        "$set": {"status": "approved", "approved_at": datetime.now(timezone.utc).isoformat()}
    })
    
    await update_user_balance(request["user_id"], request["amount"])
    
    try:
        await context.bot.send_message(
            request["user_id"],
            f"✅ تم الموافقة على طلب الشحن!\n\n💵 تم إضافة {request['amount']:,} ل.س",
            reply_markup=get_main_menu()
        )
    except:
        pass
    
    await query.edit_message_text(f"✅ تم الموافقة\n\n💵 {request['amount']:,} ل.س\n👤 {request.get('first_name', 'N/A')}")

async def reject_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return
    
    request_id = query.data.split("_")[-1]
    
    try:
        request = await db_find_one("recharge_requests", {"_id": ObjectId(request_id)})
    except:
        await query.edit_message_text("❌ لم يتم العثور على الطلب")
        return
    
    if not request:
        await query.edit_message_text("❌ لم يتم العثور على الطلب")
        return
    
    await db_update_one("recharge_requests", {"_id": ObjectId(request_id)}, {
        "$set": {"status": "rejected", "rejected_at": datetime.now(timezone.utc).isoformat()}
    })
    
    try:
        await context.bot.send_message(request["user_id"], "❌ تم رفض طلب الشحن", reply_markup=get_main_menu())
    except:
        pass
    
    await query.edit_message_text(f"❌ تم الرفض\n\n👤 {request.get('first_name', 'N/A')}")

# ==================== Cancel ====================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء", reply_markup=get_main_menu())
    return ConversationHandler.END

# ==================== Database Init ====================

async def init_database():
    """Initialize database"""
    try:
        if db is None:
            logger.error("Database not connected")
            return
        
        # Exchange rate
        rate = await db_find_one("settings", {"key": "exchange_rate"})
        if not rate:
            await db_insert_one("settings", {"key": "exchange_rate", "value": 120})
        
        # Default product prices
        for product in DEFAULT_PRODUCTS:
            price = await db_find_one("settings", {"key": f"price_{product['key']}"})
            if not price:
                await db_insert_one("settings", {"key": f"price_{product['key']}", "value": 0})
        
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database init error: {e}")

# ==================== Main ====================

def main():
    """Main function"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Conversation handlers
    syriatel_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(syriatel_confirm, pattern="^syriatel_confirm$")],
        states={
            SYRIATEL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, syriatel_amount_received)],
            SYRIATEL_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, syriatel_code_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    shamcash_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(shamcash_confirm, pattern="^shamcash_confirm$")],
        states={
            SHAMCASH_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, shamcash_amount_received)],
            SHAMCASH_PROOF: [MessageHandler(filters.PHOTO, shamcash_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    cwallet_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(cwallet_confirm, pattern="^cwallet_confirm$")],
        states={
            CWALLET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cwallet_amount_received)],
            CWALLET_PROOF: [MessageHandler(filters.PHOTO, cwallet_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    coinex_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(coinex_confirm, pattern="^coinex_confirm$")],
        states={
            COINEX_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, coinex_amount_received)],
            COINEX_PROOF: [MessageHandler(filters.PHOTO, coinex_proof_received)],
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
            ADMIN_ADD_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_account_received)],
            ADMIN_ADD_ACCOUNT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_account_price_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(admin_back, pattern="^admin_back$")],
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
            ADMIN_MANUAL_BALANCE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manual_balance_user_received)],
            ADMIN_MANUAL_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manual_balance_amount_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    
    application.add_handler(syriatel_handler)
    application.add_handler(shamcash_handler)
    application.add_handler(cwallet_handler)
    application.add_handler(coinex_handler)
    application.add_handler(buy_handler)
    application.add_handler(admin_add_account_handler)
    application.add_handler(admin_broadcast_handler)
    application.add_handler(admin_add_product_handler)
    application.add_handler(admin_manual_balance_handler)
    
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
    
    print("Bot started...")
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    import asyncio
    asyncio.get_event_loop().run_until_complete(init_database())
    main()
