import logging
import os
import httpx
from datetime import datetime, timedelta
from telegram import Update, LabeledPrice, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, PreCheckoutQueryHandler,
    filters, ContextTypes
)
from telegram.request import HTTPXRequest
from config import BOT_TOKEN, PROVIDER_TOKEN, SUBSCRIPTION_PRICE, SUBSCRIPTION_DAYS
from database import init_db, is_active, get_subscription_until, set_subscription, get_answer_mode, set_answer_mode, check_repeat_request
from deepseek_solver import solve_with_deepseek

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Прокси (оставьте как есть или настройте)
PROXY_URL = ""
if not PROXY_URL:
    PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or ""

if PROXY_URL:
    proxies = {"http://": PROXY_URL, "https://": PROXY_URL}
    http_client = httpx.AsyncClient(proxies=proxies, timeout=30.0)
    request_handler = HTTPXRequest(client=http_client)
    logger.info(f"🔌 Бот использует прокси: {PROXY_URL}")
else:
    request_handler = None
    logger.info("🔌 Прокси не задан, работаем напрямую")

# --- Клавиатура с кнопками (на русском) ---
main_keyboard = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📝 Короткий ответ"), KeyboardButton("📖 Подробный ответ")],
        [KeyboardButton("ℹ️ Статус"), KeyboardButton("⭐ Купить доступ")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

# --- Обработчики кнопок ---
async def short_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_answer_mode(user_id, 0)
    await update.message.reply_text("✅ Короткий режим (только результат).", reply_markup=main_keyboard)

async def full_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_answer_mode(user_id, 1)
    await update.message.reply_text("✅ Подробный режим (с объяснениями).", reply_markup=main_keyboard)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    until = get_subscription_until(user_id)
    mode = get_answer_mode(user_id)
    mode_text = "подробный (с объяснениями)" if mode else "короткий (только результат)"
    if until and until > datetime.now():
        days_left = (until - datetime.now()).days
        await update.message.reply_text(
            f"✅ Подписка активна, осталось {days_left} дней.\nРежим ответа: {mode_text}",
            reply_markup=main_keyboard
        )
    else:
        await update.message.reply_text(
            f"❌ Нет активной подписки.\nРежим ответа: {mode_text}\nИспользуйте /subscribe или кнопку 'Купить доступ'.",
            reply_markup=main_keyboard
        )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = "Подписка на решение домашних заданий на месяц"
    description = f"Доступ к боту-репетитору на {SUBSCRIPTION_DAYS} дней. Безлимитные задачи."
    payload = "monthly_subscription"
    currency = "XTR"
    prices = [LabeledPrice("Месяц доступа", SUBSCRIPTION_PRICE)]
    await context.bot.send_invoice(
        chat_id=chat_id, title=title, description=description,
        payload=payload, provider_token=PROVIDER_TOKEN, currency=currency,
        prices=prices, need_name=False, need_phone_number=False, need_email=False
    )

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload == "monthly_subscription":
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Неверный платёж")

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_until = datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)
    set_subscription(user_id, new_until)
    await update.message.reply_text(
        f"✅ Оплата получена! Подписка активна до {new_until.strftime('%d.%m.%Y')}.\n"
        f"Теперь отправляйте мне задачи текстом или фото, и я помогу решить.",
        reply_markup=main_keyboard
    )

# --- Временное отключение проверки подписки (как вы просили) ---
def require_subscription(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Временно отключаем проверку подписки
        return await func(update, context)
    return wrapper

async def get_file_url(file_id: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        file_obj = await context.bot.get_file(file_id)
        file_path = file_obj.file_path
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        return file_url
    except Exception as e:
        logger.error(f"Ошибка получения URL файла: {e}")
        return None

@require_subscription
async def handle_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверка на длинное сообщение (>1500 символов)
    if update.message.text and len(update.message.text) > 1500:
        await update.message.reply_text("❌ Сообщение слишком длинное (более 1500 символов). Пожалуйста, сократите условие задачи.", reply_markup=main_keyboard)
        return
    
    # Если это текст кнопки – не обрабатываем как задачу
    if update.message.text in ["📝 Короткий ответ", "📖 Подробный ответ", "ℹ️ Статус", "⭐ Купить доступ"]:
        return
    
    if update.message.text:
        problem_text = update.message.text
        image_url = None
    elif update.message.photo:
        photo = update.message.photo[-1]
        image_url = await get_file_url(photo.file_id, context)
        if not image_url:
            await update.message.reply_text("❌ Не удалось загрузить фото. Попробуйте ещё раз.", reply_markup=main_keyboard)
            return
        problem_text = update.message.caption or "Реши задачу с этого фото."
    else:
        await update.message.reply_text("Пожалуйста, напишите условие задачи текстом или пришлите фото.", reply_markup=main_keyboard)
        return

    # Проверка на повтор одинаковой задачи в течение 10 секунд
    user_id = update.effective_user.id
    if check_repeat_request(user_id, problem_text):
        await update.message.reply_text("⚠️ Вы только что отправляли эту задачу. Подождите 10 секунд перед повторной отправкой.", reply_markup=main_keyboard)
        return

    mode = get_answer_mode(user_id)
    await update.message.chat.send_action(action="typing")
    try:
        answer = solve_with_deepseek(problem_text, image_url, mode)
    except Exception as e:
        logger.error(f"Ошибка при вызове solve_with_deepseek: {e}")
        answer = "⚠️ Произошла внутренняя ошибка. Попробуйте позже."

    if len(answer) > 4000:
        for chunk in [answer[i:i+4000] for i in range(0, len(answer), 4000)]:
            await update.message.reply_text(chunk, reply_markup=main_keyboard)
    else:
        await update.message.reply_text(answer, reply_markup=main_keyboard)
    
    if update.message.text:
        problem_text = update.message.text
        image_url = None
    elif update.message.photo:
        photo = update.message.photo[-1]
        image_url = await get_file_url(photo.file_id, context)
        if not image_url:
            await update.message.reply_text("❌ Не удалось загрузить фото. Попробуйте ещё раз.", reply_markup=main_keyboard)
            return
        problem_text = update.message.caption or "Реши задачу с этого фото."
    else:
        await update.message.reply_text("Пожалуйста, напишите условие задачи текстом или пришлите фото.", reply_markup=main_keyboard)
        return

    mode = get_answer_mode(update.effective_user.id)
    await update.message.chat.send_action(action="typing")
    try:
        answer = solve_with_deepseek(problem_text, image_url, mode)
    except Exception as e:
        logger.error(f"Ошибка при вызове solve_with_deepseek: {e}")
        answer = "⚠️ Произошла внутренняя ошибка. Попробуйте позже."

    if len(answer) > 4000:
        for chunk in [answer[i:i+4000] for i in range(0, len(answer), 4000)]:
            await update.message.reply_text(chunk, reply_markup=main_keyboard)
    else:
        await update.message.reply_text(answer, reply_markup=main_keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧑‍🎓 Привет! Я помогу с домашкой по математике и программированию.\n\n"
        "Используй кнопки внизу, чтобы выбрать режим ответа:\n"
        "• 📝 Короткий ответ – только результат\n"
        "• 📖 Подробный ответ – с объяснениями (без лишних символов)\n"
        "• ℹ️ Статус – информация о подписке\n"
        "• ⭐ Купить доступ – оплата Telegram Stars\n\n"
        "Просто напиши задачу текстом или отправь фото.",
        reply_markup=main_keyboard
    )

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Неизвестная команда. Используйте кнопки или просто напишите задачу.", reply_markup=main_keyboard)

def main():
    init_db()
    app_builder = Application.builder().token(BOT_TOKEN)
    if request_handler:
        app_builder = app_builder.request(request_handler)
    app = app_builder.build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("short", short_mode))
    app.add_handler(CommandHandler("full", full_mode))
    
    # Обработчики для кнопок (через MessageHandler с фильтром текста)
    app.add_handler(MessageHandler(filters.Regex("^📝 Короткий ответ$"), short_mode))
    app.add_handler(MessageHandler(filters.Regex("^📖 Подробный ответ$"), full_mode))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Статус$"), status_command))
    app.add_handler(MessageHandler(filters.Regex("^⭐ Купить доступ$"), subscribe))
    
    # Обработчик задач (текст и фото), но исключаем текст кнопок
    app.add_handler(MessageHandler(
        (filters.TEXT & ~filters.COMMAND & ~filters.Regex("^(📝 Короткий ответ|📖 Подробный ответ|ℹ️ Статус|⭐ Купить доступ)$")) | filters.PHOTO,
        handle_task
    ))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    logger.info("Бот с DeepSeek запущен")
    app.run_polling()

if __name__ == "__main__":
    main()

