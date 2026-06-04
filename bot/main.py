import asyncio
import logging
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import BOT_TOKEN, POLLING_INTERVAL
from handlers import start_help, auth, tickets
from services.scheduler import check_updates

async def main():
    # 1. Logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )
    
    # 3. Init Bot & Dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # 4. Include routers
    dp.include_router(start_help.router)
    dp.include_router(auth.router)
    dp.include_router(tickets.router)
    
    # 5. Start APScheduler for background tasks
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_updates, 'interval', seconds=POLLING_INTERVAL, args=[bot])
    scheduler.start()
    
    # 6. Start Bot Polling
    try:
        await dp.start_polling(bot)
    finally:
        # Graceful shutdown
        scheduler.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
