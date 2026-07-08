import os
import asyncio
import logging
import random
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiohttp import web
import psycopg  # Supabase (PostgreSQL) uchun asinxron drayver
from psycopg.rows import tuple_row

# --- ATROF-MUHIT O'ZGARUVCHILARI (ENV) ---
BOT_TOKEN = "8855330752:AAGR9FIUA0Fz2Xu9enTJD08gPCR7p5UNXBI"
ADMIN_ID = 5111794979
MAIN_GROUP_ID = -549231963

PORT = int(os.getenv("PORT", "8080"))
DATABASE_URL = os.getenv("DATABASE_URL")  # Supabase URI ulanishi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class RegistrationState(StatesGroup):
    waiting_for_name = State()       

class TransferState(StatesGroup):
    waiting_for_level = State()      
    waiting_for_amount = State()     
    waiting_for_reason = State()     
    confirm_transfer = State()        

class AdminManageState(StatesGroup):
    waiting_for_bonus = State()       
    waiting_for_deduct = State()      

# --- SUPABASE (POSTGRESQL) ULANISH FUNKSIYALARI ---
async def get_db_connection():
    return await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=tuple_row)

async def get_user(telegram_id):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT telegram_id, full_name, give_balance, earned_balance, is_approved, last_spin_date FROM users WHERE telegram_id = %s", 
                (telegram_id,)
            )
            return await cursor.fetchone()

async def get_all_approved_users(exclude_id=None):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            if exclude_id:
                await cursor.execute(
                    "SELECT telegram_id, full_name FROM users WHERE is_approved = TRUE AND telegram_id != %s AND telegram_id != %s", 
                    (exclude_id, ADMIN_ID)
                )
            else:
                await cursor.execute(
                    "SELECT telegram_id, full_name FROM users WHERE is_approved = TRUE AND telegram_id != %s", 
                    (ADMIN_ID,)
                )
            return await cursor.fetchall()

async def update_user_balance(telegram_id, column, amount, operation="+"):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            query = f"UPDATE users SET {column} = {column} {operation} %s WHERE telegram_id = %s"
            await cursor.execute(query, (amount, telegram_id))
            await conn.commit()

async def update_spin_date(telegram_id, date_str):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("UPDATE users SET last_spin_date = %s WHERE telegram_id = %s", (date_str, telegram_id))
            await conn.commit()

async def add_pending_user(telegram_id, full_name):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO users (telegram_id, full_name, is_approved) VALUES (%s, %s, FALSE) ON CONFLICT (telegram_id) DO NOTHING", 
                (telegram_id, full_name)
            )
            await conn.commit()

async def add_admin_automatically(telegram_id):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO users (telegram_id, full_name, is_approved) VALUES (%s, 'Asosiy Admin', TRUE) ON CONFLICT (telegram_id) DO UPDATE SET is_approved = TRUE", 
                (telegram_id,)
            )
            await conn.commit()

async def approve_user_db(telegram_id):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("UPDATE users SET is_approved = TRUE WHERE telegram_id = %s", (telegram_id,))
            await conn.commit()

async def reject_user_db(telegram_id):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("DELETE FROM users WHERE telegram_id = %s", (telegram_id,))
            await conn.commit()

async def execute_transfer(sender_id, receiver_id, amount, reason):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("UPDATE users SET give_balance = give_balance - %s WHERE telegram_id = %s", (amount, sender_id))
            await cursor.execute("UPDATE users SET earned_balance = earned_balance + %s WHERE telegram_id = %s", (amount, receiver_id))
            await cursor.execute(
                "INSERT INTO history (sender_id, receiver_id, amount, reason, timestamp) VALUES (%s, %s, %s, %s, %s)", 
                (sender_id, receiver_id, amount, reason, now_str)
            )
            await conn.commit()

async def get_top_5():
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT full_name, earned_balance FROM users WHERE is_approved = TRUE AND telegram_id != %s ORDER BY earned_balance DESC LIMIT 5", 
                (ADMIN_ID,)
            )
            return await cursor.fetchall()

async def get_user_history(telegram_id):
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            if telegram_id == ADMIN_ID:
                await cursor.execute("""
                    SELECT h.amount, h.reason, h.timestamp, u1.full_name, u2.full_name, h.sender_id 
                    FROM history h
                    JOIN users u1 ON h.sender_id = u1.telegram_id
                    JOIN users u2 ON h.receiver_id = u2.telegram_id
                    ORDER BY h.id DESC LIMIT 20
                """)
            else:
                await cursor.execute("""
                    SELECT h.amount, h.reason, h.timestamp, u1.full_name, u2.full_name, h.sender_id 
                    FROM history h
                    JOIN users u1 ON h.sender_id = u1.telegram_id
                    JOIN users u2 ON h.receiver_id = u2.telegram_id
                    WHERE h.sender_id = %s OR h.receiver_id = %s
                    ORDER BY h.id DESC LIMIT 10
                """, (telegram_id, telegram_id))
            return await cursor.fetchall()

async def distribute_monthly_coins():
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("UPDATE users SET give_balance = 50.0 WHERE is_approved = TRUE")
            await conn.commit()

async def reset_all_data():
    async with await get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("TRUNCATE TABLE history RESTART IDENTITY CASCADE")
            await cursor.execute("DELETE FROM users WHERE telegram_id != %s", (ADMIN_ID,))
            await cursor.execute("UPDATE users SET give_balance = 50.0, earned_balance = 0.0 WHERE telegram_id = %s", (ADMIN_ID,))
            await conn.commit()


# --- TUGMALAR ---
def get_inline_menu(user_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Coin O'tkazish", callback_data="menu_transfer")
    builder.button(text="📊 Mening Hisobim", callback_data="menu_balance")
    builder.button(text="🏆 TOP-5 Reyting", callback_data="menu_top")
    builder.button(text="🔄 O'tkazmalar Tarixi", callback_data="menu_history")
    builder.button(text="🎰 Kunlik Omadli Coin", callback_data="menu_spin")
    builder.button(text="ℹ️ Bot Maqsadi", callback_data="menu_purpose")
    if user_id == ADMIN_ID:
        builder.button(text="⚙️ Admin Panel", callback_data="admin_panel")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()

def get_back_button():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Bosh Menyu", callback_data="menu_main")
    return builder.as_markup()

def get_admin_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Xodimlarni Boshqarish", callback_data="admin_manage_users")
    builder.button(text="💰 Hammaga 50 coin tarqatish", callback_data="admin_distribute")
    builder.button(text="🚨 BOTNI RESET QILISH", callback_data="admin_reset_confirm")
    builder.button(text="⬅️ Bosh Menyu", callback_data="menu_main")
    builder.adjust(1)
    return builder.as_markup()

def get_purpose_text():
    return (
        f"🚀 **Ushbu bot nima maqsadda ochilgan?**\n"
        f"Botimiz jamoamiz ichidagi hamjihatlikni, o'zaro ko'makni va minnatdorchilik "
        f"madaniyatini oshirish uchun yaratilgan **Ichki Rag'batlantirish Tizimi** hisoblanadi.\n\n"
        f"💡 **Bot qoidalari va ishlash tartibi:**\n"
        f"1️⃣ Har oy boshida har bir xodimga **50 'Berish Coini'** taqdim etiladi.\n"
        f"2️⃣ Siz bu coinlarni o'zingizga olib qololmaysiz. Ularni oy davomida sizga yordam bergan, "
        f"qiyin vaziyatda qo'llagan yoki o'z ishini a'lo darajada bajargan **hamkasblaringizga yuborishingiz kerak**.\n"
        f"3️⃣ Hamkasblaringiz yuborgan coinlar sizning **'Yiqqan balansizda'** to'planadi va bu oylik reytingingizni belgilaydi.\n"
        f"4️⃣ Kunlik `🎰 Kunlik Omadli Coin` tugmasini bosib, balansingizni bepul oshirib borishingiz mumkin.\n\n"
        f"🤝 _Keling, bir-birimizni qo'llab-quvvatlab, jamoaviy ruhni yuqori ko'taramiz!_"
    )


# --- HANDLERLAR ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == ADMIN_ID:
        await add_admin_automatically(ADMIN_ID)
        await message.answer("👋 Xush kelibsiz, *Asosiy Admin*!\n\nQuyidagi menyudan foydalanishingiz mumkin:", parse_mode="Markdown", reply_markup=get_inline_menu(message.from_user.id))
        return

    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("🚀 Roʻyxatdan oʻtish uchun **Ism va familiyangizni kiriting:**\n(Masalan: *Asilbek Olimov*)", parse_mode="Markdown")
        await state.set_state(RegistrationState.waiting_for_name)
    elif user[4] is False:
        await message.answer("⏳ **Sizning soʻrovingiz hamon admin tasdigʻini kutmoqda.**")
    else:
        await message.answer(text=f"👋 Xush kelibsiz, *{user[1]}*!\n\nKerakli bo'limni tanlang:", parse_mode="Markdown", reply_markup=get_inline_menu(message.from_user.id))

@dp.message(RegistrationState.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 4:
        await message.answer("❌ Ism juda qisqa. Qaytadan kiriting:")
        return
        
    await add_pending_user(message.from_user.id, full_name)
    await state.clear()
    await message.answer("⏳ **Soʻrovingiz adminga yuborildi. Tasdiqlangach botdan to'liq foydalanishingiz mumkin.**")
    
    admin_builder = InlineKeyboardBuilder()
    admin_builder.button(text="✅ Tasdiqlash", callback_data=f"approve:{message.from_user.id}")
    admin_builder.button(text="❌ Rad etish", callback_data=f"reject:{message.from_user.id}")
    admin_builder.adjust(2)
    
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 **Yangi xodim soʻrovi!**\n\n👤 Ismi: *{full_name}*\n🆔 ID: `{message.from_user.id}`",
            parse_mode="Markdown",
            reply_markup=admin_builder.as_markup()
        )
    except Exception as e:
        logger.error(f"Adminga xabar yuborib bo'lmadi: {e}")

# --- ADMIN PANEL ---
@dp.callback_query(F.data == "admin_panel")
async def show_admin_panel(callback: types.CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("⚙️ **Admin Panel boshqaruv oynasi:**\n\nKerakli amalni tanlang 👇", reply_markup=get_admin_menu())

@dp.callback_query(F.data == "admin_manage_users")
async def admin_manage_users(callback: types.CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID: return
    users = await get_all_approved_users()  
    builder = InlineKeyboardBuilder()
    if not users:
        await callback.message.edit_text("👤 Hozircha tasdiqlangan xodimlar yo'q.", reply_markup=get_admin_menu())
        return
    for user_id, full_name in users:
        builder.button(text=f"👤 {full_name}", callback_data=f"admin_user_profile:{user_id}")
    builder.button(text="⬅️ Orqaga", callback_data="admin_panel")
    builder.adjust(1)
    await callback.message.edit_text("⚙️ **Boshqarmoqchi bo'lgan xodimingizni tanlang:**", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("admin_user_profile:"))
async def admin_user_profile(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID: return
    await state.clear()
    user_id = int(callback.data.split(":")[1])
    user = await get_user(user_id)
    if not user:
        await callback.message.edit_text("❌ Foydalanuvchi topilmadi.", reply_markup=get_admin_menu())
        return
    text = (
        f"👤 **Xodim:** {user[1]}\n🆔 **Telegram ID:** `{user[0]}`\n---------------------------\n"
        f"🎁 **Yuborish balansi:** {user[2]} coin\n💰 **Yiqqan balansi:** {user[3]} coin"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Coin qo'shish", callback_data=f"admin_coin_add:{user_id}")
    builder.button(text="➖ Coin ayirish", callback_data=f"admin_coin_sub:{user_id}")
    builder.button(text="❌ Tizimdan o'chirish (BAN)", callback_data=f"admin_user_ban:{user_id}")
    builder.button(text="⬅️ Xodimlar ro'yxati", callback_data="admin_manage_users")
    builder.adjust(2, 1, 1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("admin_coin_add:"))
async def admin_coin_add_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = int(callback.data.split(":")[1])
    await state.update_data(target_user_id=user_id, menu_msg_id=callback.message.message_id)
    await callback.message.edit_text("💰 Ushbu xodimning **Yiqqan balansiga** qancha coin qo'shmoqchisiz?\n\nFaqat musbat son kiriting:")
    await state.set_state(AdminManageState.waiting_for_bonus)

@dp.message(AdminManageState.waiting_for_bonus)
async def admin_coin_add_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    if not message.text.replace('.', '', 1).isdigit():
        await message.answer("❌ Faqat musbat son kiriting:")
        return
    amount = float(message.text)
    data = await state.get_data()
    user_id = data['target_user_id']
    await update_user_balance(user_id, "earned_balance", amount, operation="+")
    user = await get_user(user_id)
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Xodim profiliga qaytish", callback_data=f"admin_user_profile:{user_id}")
    await message.answer(f"✅ *{user[1]}*ga {amount} coin qo'shildi!", parse_mode="Markdown", reply_markup=builder.as_markup())
    try: await bot.send_message(user_id, f"🎉 Admin tomonidan balansingizga **{amount} coin** bonus qo'shildi!")
    except Exception: pass

@dp.callback_query(F.data.startswith("admin_coin_sub:"))
async def admin_coin_sub_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = int(callback.data.split(":")[1])
    await state.update_data(target_user_id=user_id, menu_msg_id=callback.message.message_id)
    await callback.message.edit_text("⚠️ Ushbu xodimning **Yiqqan balansidan** qancha coin ayirmoqchisiz?\n\nFaqat musbat son kiriting:")
    await state.set_state(AdminManageState.waiting_for_deduct)

@dp.message(AdminManageState.waiting_for_deduct)
async def admin_coin_sub_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    if not message.text.replace('.', '', 1).isdigit():
        await message.answer("❌ Faqat musbat son kiriting:")
        return
    amount = float(message.text)
    data = await state.get_data()
    user_id = data['target_user_id']
    await update_user_balance(user_id, "earned_balance", amount, operation="-")
    user = await get_user(user_id)
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Xodim profiliga qaytish", callback_data=f"admin_user_profile:{user_id}")
    await message.answer(f"✅ *{user[1]}*ning balansidan {amount} coin chegirildi!", parse_mode="Markdown", reply_markup=builder.as_markup())
    try: await bot.send_message(user_id, f"⚠️ Admin tomonidan balansingizdan **{amount} coin** chegirildi.")
    except Exception: pass

@dp.callback_query(F.data.startswith("admin_user_ban:"))
async def admin_user_ban(callback: types.CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID: return
    user_id = int(callback.data.split(":")[1])
    user = await get_user(user_id)
    if user:
        await reject_user_db(user_id)
        await callback.message.edit_text(f"❌ *{user[1]}* muvaffaqiyatli tizimdan o'chirildi.", parse_mode="Markdown", reply_markup=get_admin_menu())
        try: await bot.send_message(user_id, "❌ Siz botdan admin tomonidan chetlashtirildingiz.")
        except Exception: pass

@dp.callback_query(F.data.startswith("approve:"))
async def admin_approve(callback: types.CallbackQuery):
    await callback.answer()
    user_id = int(callback.data.split(":")[1])
    user = await get_user(user_id)
    if user:
        await approve_user_db(user_id)
        try: await callback.message.delete()
        except TelegramBadRequest: pass
        await bot.send_message(chat_id=ADMIN_ID, text=f"✅ *{user[1]}* tasdiqlandi va tizimga qo'shildi!", parse_mode="Markdown", reply_markup=get_inline_menu(ADMIN_ID))
        try: await bot.send_message(user_id, "🎉 Admin sizni tasdiqladi! Qaytadan /start bosing.")
        except Exception: pass

@dp.callback_query(F.data.startswith("reject:"))
async def admin_reject(callback: types.CallbackQuery):
    await callback.answer()
    user_id = int(callback.data.split(":")[1])
    user = await get_user(user_id)
    if user:
        await reject_user_db(user_id)
        try: await callback.message.delete()
        except TelegramBadRequest: pass
        await bot.send_message(chat_id=ADMIN_ID, text=f"❌ *{user[1]}* so'rovi rad etildi va o'chirildi.", parse_mode="Markdown", reply_markup=get_inline_menu(ADMIN_ID))
        try: await bot.send_message(user_id, "❌ So'rovingiz rad etildi.")
        except Exception: pass

@dp.callback_query(F.data == "admin_distribute")
async def admin_distribute_coins(callback: types.CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID: return
    await distribute_monthly_coins()
    await callback.message.edit_text("💰 Balanslar muvaffaqiyatli yangilandi (Hammaga 50 berish coini).", reply_markup=get_back_button())

@dp.callback_query(F.data == "admin_reset_confirm")
async def admin_reset_confirm(callback: types.CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID: return
    builder = InlineKeyboardBuilder()
    builder.button(text="💥 HA, BARCHASINI O'CHIRISH", callback_data="admin_reset_execute")
    builder.button(text="❌ Yo'q, bekor qilish", callback_data="admin_panel")
    builder.adjust(1)
    await callback.message.edit_text("⚠️ **DIQQAT! Rostdan ham bot ma'lumotlarini tozalab yubormoqchimisiz?**", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "admin_reset_execute")
async def admin_reset_execute(callback: types.CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID: return
    await reset_all_data()
    await callback.message.edit_text("🚨 **Bot muvaffaqiyatli reset qilindi!**", reply_markup=get_back_button())


# --- STANDARD MENYULAR ---
@dp.callback_query(F.data == "menu_main")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user = await get_user(callback.from_user.id)
    if callback.from_user.id == ADMIN_ID:
        await callback.message.edit_text("👋 Xush kelibsiz, *Asosiy Admin*!\n\nQuyidagi menyudan foydalanishingiz mumkin:", parse_mode="Markdown", reply_markup=get_inline_menu(callback.from_user.id))
    else:
        await callback.message.edit_text(text=f"👋 Xush kelibsiz, *{user[1]}*!\n\nKerakli bo'limni tanlang:", parse_mode="Markdown", reply_markup=get_inline_menu(callback.from_user.id))

@dp.callback_query(F.data == "menu_purpose")
async def show_purpose(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(text=get_purpose_text(), parse_mode="Markdown", reply_markup=get_back_button())

@dp.callback_query(F.data == "menu_balance")
async def show_balance_inline(callback: types.CallbackQuery):
    await callback.answer()
    user = await get_user(callback.from_user.id)
    await callback.message.edit_text(f"📊 **Balans:**\n🎁 Berish uchun: {user[2]} coin\n💰 Yiqqan: {user[3]} coin", parse_mode="Markdown", reply_markup=get_back_button())

@dp.callback_query(F.data == "menu_id")
async def show_id_inline(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(f"🆔 **ID raqamingiz:**\n`{callback.from_user.id}`", parse_mode="Markdown", reply_markup=get_back_button())

@dp.callback_query(F.data == "menu_top")
async def show_top_5(callback: types.CallbackQuery):
    await callback.answer()
    top_list = await get_top_5()
    text = "🏆 **TOP-5 Reyting (Faqat xodimlar):**\n\n"
    if not top_list: text += "_Hali bo'sh_"
    else:
        for i, row in enumerate(top_list): text += f"{i+1}. *{row[0]}* — `{row[1]} coin`\n"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_button())


# --- O'TKAZMALAR TARIXI ---
@dp.callback_query(F.data == "menu_history")
async def show_history(callback: types.CallbackQuery):
    await callback.answer()
    rows = await get_user_history(callback.from_user.id)
    
    if callback.from_user.id == ADMIN_ID:
        text = "📋 **Tizimdagi barcha global o'tkazmalar (Admin ko'rinishi):**\n\n"
    else:
        text = "🔄 **Sizning oxirgi 10 ta o'tkazma tarixingiz:**\n\n"
        
    if not rows:
        text += "_Hozircha o'tkazmalar mavjud emas._"
    else:
        for amount, reason, timestamp, sender, receiver, s_id in rows:
            if callback.from_user.id == ADMIN_ID:
                text += f"👤 *{sender}* ➡️ *{receiver}*\n💰 {amount} coin | 🎯 Sabab: _{reason}_\n📅 _{timestamp}_\n---------------------\n"
            else:
                if s_id == callback.from_user.id:
                    text += f"📤 *Yuborildi:* {amount} coin ➡️ *{receiver}*\n"
                else:
                    text += f"📥 *Olingan:* {amount} coin ⬅️ *{sender}*\n"
                text += f"🎯 *Sabab:* _{reason}_\n📅 _{timestamp}_\n---------------------\n"
                
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_button())


# --- KUNLIK OMADLI COIN ---
@dp.callback_query(F.data == "menu_spin")
async def daily_spin(callback: types.CallbackQuery):
    await callback.answer()
    user = await get_user(callback.from_user.id)
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    if user and user[5] == today_str:
        await callback.message.edit_text("⏳ **Siz bugun o'z omadingizni sinab ko'rgansiz!**\n\nYana 1 ta imkoniyat ertaga beriladi.", reply_markup=get_back_button())
        return
        
    bonus = random.choice([0.5, 1.0])
    await update_user_balance(callback.from_user.id, "give_balance", bonus, operation="+")
    await update_spin_date(callback.from_user.id, today_str)
    
    await callback.message.edit_text(
        f"🎰 **Omadli g'ildirak aylandi!**\n\n🎉 Tabriklaymiz! Sizning berish balansigizga **+{bonus} coin** qo'shildi.\n"
        f"Ushbu mablag'ni hamkasblaringizni rag'batlantirish uchun sarflashingiz mumkin!", 
        reply_markup=get_back_button()
    )


# --- COIN O'TKAZISH TIZIMI ---
@dp.callback_query(F.data == "menu_transfer")
async def start_transfer_inline(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    users = await get_all_approved_users(callback.from_user.id) 
    builder = InlineKeyboardBuilder()
    if not users:
        await callback.message.edit_text("👤 **Botda hozircha boshqa tasdiqlangan xodimlar mavjud emas.**", reply_markup=get_back_button())
        return
    for user_id, full_name in users:
        builder.button(text=f"👤 {full_name}", callback_data=f"select_user:{user_id}")
    builder.button(text="⬅️ Bosh Menyu", callback_data="menu_main")
    builder.adjust(1)
    await callback.message.edit_text("✨ **Coin yubormoqchi boʻlgan hamkasbizni tanlang:**", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("select_user:"))
async def process_selected_user(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    receiver_id = int(callback.data.split(":")[1])
    receiver = await get_user(receiver_id)
    if not receiver:
        await callback.message.edit_text("❌ Xodim topilmadi.", reply_markup=get_back_button())
        return
    await state.update_data(receiver_id=receiver_id, receiver_name=receiver[1])
    
    text = (
        f"👤 **Qabul qiluvchi:** *{receiver[1]}*\n\n"
        f"✨ **Ezgu ish darajasini tanlang:**\n\n"
        f"🟢 **LEVEL 1 (2–3 Coin)** — *Kichik yordam.*\n"
        f"🟡 **LEVEL 2 (4–5 Coin)** — *Professional hissa.*\n"
        f"🔴 **LEVEL 3 (6–7 Coin)** — *Katta qahramonlik.*"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 LEVEL 1", callback_data="level:1")
    builder.button(text="🟡 LEVEL 2", callback_data="level:2")
    builder.button(text="🔴 LEVEL 3", callback_data="level:3")
    builder.button(text="⬅️ Orqaga", callback_data="menu_transfer")
    builder.adjust(3, 1)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    await state.set_state(TransferState.waiting_for_level)

@dp.callback_query(F.data.startswith("level:"))
async def process_level_selection(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    level = int(callback.data.split(":")[1])
    await state.update_data(selected_level=level)
    data = await state.get_data()
    receiver_name = data['receiver_name']
    builder = InlineKeyboardBuilder()
    
    if level == 1:
        text = f"🟢 **LEVEL 1 tanlandi**\n\n👤 Kimga: *{receiver_name}*\n\n👇 **Oʻtkazma miqdorini tanlang:**"
        builder.button(text="2 Coin", callback_data="amt:2")
        builder.button(text="3 Coin", callback_data="amt:3")
    elif level == 2:
        text = f"🟡 **LEVEL 2 tanlandi**\n\n👤 Kimga: *{receiver_name}*\n\n👇 **Oʻtkazma miqdorini tanlang:**"
        builder.button(text="4 Coin", callback_data="amt:4")
        builder.button(text="5 Coin", callback_data="amt:5")
    else:
        text = f"🔴 **LEVEL 3 tanlandi**\n\n👤 Kimga: *{receiver_name}*\n\n👇 **Oʻtkazma miqdorini tanlang:**"
        builder.button(text="6 Coin", callback_data="amt:6")
        builder.button(text="7 Coin", callback_data="amt:7")
        
    builder.button(text="⬅️ Orqaga", callback_data=f"select_user:{data['receiver_id']}")
    builder.adjust(2, 1)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    await state.set_state(TransferState.waiting_for_amount)

@dp.callback_query(F.data.startswith("amt:"))
async def process_amount_selection(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    amount = float(callback.data.split(":")[1])
    user_data = await get_user(callback.from_user.id)
    if user_data[2] < amount:
        await callback.message.edit_text(f"❌ Balansingizda mablag' yetarli emas. Sizda: {user_data[2]} coin bor.", reply_markup=get_back_button())
        await state.clear()
        return
        
    await state.update_data(amount=amount)
    data = await state.get_data()
    try: await callback.message.delete()
    except TelegramBadRequest: pass
    
    new_msg = await bot.send_message(
        chat_id=callback.message.chat.id,
        text=f"👤 Kimga: *{data['receiver_name']}*\n💸 Miqdor: *{amount} coin*\n\n📝 **O'tkazma sababini (Izoh) yozib yuboring:**",
        parse_mode="Markdown"
    )
    await state.update_data(user_msg_id=new_msg.message_id)
    await state.set_state(TransferState.waiting_for_reason)

@dp.message(TransferState.waiting_for_reason)
async def process_reason_text(message: types.Message, state: FSMContext):
    reason = message.text.strip()
    if len(reason) < 4:
        await message.answer("❌ Izoh juda qisqa. Batafsilroq yozing:")
        return
    data = await state.get_data()
    try: await bot.delete_message(chat_id=message.chat.id, message_id=data['user_msg_id'])
    except: pass
    try: await message.delete()
    except: pass

    await state.update_data(reason=reason)
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="confirm_yes")
    builder.button(text="❌ Bekor qilish", callback_data="confirm_no")
    builder.adjust(2)
    
    await message.answer(
        f"❓ **O'tkazmani tasdiqlaysizmi?**\n\n👤 **Kimga:** {data['receiver_name']}\n💸 **Miqdor:** {data['amount']} coin\n🎯 **Izoh:** _{reason}_",
        parse_mode="Markdown", reply_markup=builder.as_markup()
    )
    await state.set_state(TransferState.confirm_transfer)

@dp.callback_query(TransferState.confirm_transfer)
async def complete_transfer_inline(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data == "confirm_yes":
        data = await state.get_data()
        sender_name = (await get_user(callback.from_user.id))[1]
        
        await execute_transfer(callback.from_user.id, data['receiver_id'], data['amount'], data['reason'])
        await callback.message.edit_text("✅ Muvaffaqiyatli bajarildi!", reply_markup=get_back_button())
        
        try:
            await bot.send_message(data['receiver_id'], f"🎉 *{sender_name}* sizga {data['amount']} coin yubordi!\n🎯 Sabab: _{data['reason']}_", parse_mode="Markdown")
        except Exception: pass
        
        try:
            log_text = (
                f"🎉 **Jamoada yangi minnatdorchilik!**\n\n"
                f"👤 **Yuboruvchi:** {sender_name}\n"
                f"👤 **Qabul qiluvchi:** {data['receiver_name']}\n"
                f"💸 **Miqdor:** {data['amount']} coin\n"
                f"🎯 **Ezgu ish / Sabab:**\n`{data['reason']}`\n\n"
                f"🤝 _Bir-biringizni qo'llab-quvvatlashda davom eting!_"
            )
            await bot.send_message(chat_id=MAIN_GROUP_ID, text=log_text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Global guruhga log yuborishda xatolik: {e}")
    else:
        await callback.message.edit_text("❌ Bekor qilindi.", reply_markup=get_back_button())
    await state.clear()


# --- OYLIK AVTOMATIK RESET ---
async def monthly_cron_job():
    await distribute_monthly_coins()
    try:
        await bot.send_message(
            chat_id=MAIN_GROUP_ID, 
            text="📅 **Yangi oy boshlandi!**\n\n🚀 Barcha faol xodimlarning 'Yuborish balansi' avtomatik ravishda **50 coin**ga yangilandi!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Oylik reset bildirishnomasida xatolik: {e}")


# --- 🌐 WEB SERVER (RENDER PORTI UCHUN) ---
async def handle_web(request):
    return web.Response(text="Bot muvaffaqiyatli ishlamoqda!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server {PORT}-portda ishga tushdi.")


# --- ASOSIY ISHGA TUSHIRISH ---
async def main():
    # 1. Cron shchedulerni yoqish
    scheduler = AsyncIOScheduler()
    scheduler.add_job(monthly_cron_job, CronTrigger(day=1, hour=0, minute=0))
    scheduler.start()
    
    # 2. Dummy serverni ishga tushirish (Render uchun)
    await start_web_server()
    
    # 3. Bot Pollingni boshlash
    if ADMIN_ID:
        await add_admin_automatically(ADMIN_ID)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())