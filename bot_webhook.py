import os
import logging
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import BOT_TOKEN, PROVIDER_TOKEN
from database import init_db, get_answer_mode, set_answer_mode
from deepseek_solver import solve_with_deepseek

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

init_db()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧑‍🎓 Привет! Я помогу с домашкой по математике и программированию.\n\n"
        "Команды:\n/short – короткий ответ (только результат)\n/full – подробный ответ с объяснениями"
    )

async def short_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_answer_mode(update.effective_user.id, 0)
    await update.message.reply_text("✅ Короткий режим (только результат)")

async def full_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_answer_mode(update.effective_user.id, 1)
    await update.message.reply_text("✅ Подробный режим (с объяснениями)")

async def handle_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text:
        await update.message.reply_text("Напишите условие задачи")
        return
    if len(text) > 1500:
        await update.message.reply_text("❌ Сообщение слишком длинное (максимум 1500 символов)")
        return
    mode = get_answer_mode(update.effective_user.id)
    try:
        answer = solve_with_deepseek(text, None, mode)
    except Exception as e:
        logger.error(f"Ошибка при решении: {e}")
        answer = "⚠️ Ошибка при решении задачи. Попробуйте позже."
    await update.message.reply_text(answer)

app = Application.builder().token(BOT_TOKEN).updater(None).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("short", short_mode))
app.add_handler(CommandHandler("full", full_mode))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_task))

async def webhook(request: Request) -> Response:
    update = Update.de_json(await request.json(), app.bot)
    await app.update_queue.put(update)
    return Response()

async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")

starlette_app = Starlette(routes=[
    Route("/telegram", webhook, methods=["POST"]),
    Route("/healthcheck", health, methods=["GET"]),
])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(starlette_app, host="0.0.0.0", port=8000)
