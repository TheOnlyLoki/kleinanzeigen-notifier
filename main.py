import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from routers import (
    inserate_ultra as inserate,
    inserat,
    inserate_detailed_ultra as inserate_detailed,
    inserate_batch,
    convert_url,
    inserate_by_url,
)
from utils.browser import OptimizedPlaywrightManager
from utils.asyncio_optimizations import EventLoopOptimizer
from notifier.service import NotifierService
from notifier.commands import TelegramCommandHandler

# Global browser manager instance for sharing across all endpoints
browser_manager = None
notifier_service = None
command_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - startup and shutdown events"""
    global browser_manager, notifier_service, command_task

    # Setup uvloop for maximum performance (2-4x improvement)
    uvloop_enabled = EventLoopOptimizer.setup_uvloop()

    # Optimize event loop settings
    EventLoopOptimizer.optimize_event_loop()

    # Startup: Initialize shared browser manager with optimized settings
    browser_manager = OptimizedPlaywrightManager(max_contexts=20, max_concurrent=10)
    await browser_manager.start()

    # Store browser manager in app state for access by routers
    app.state.browser_manager = browser_manager
    app.state.uvloop_enabled = uvloop_enabled

    # Start notifier watch loops (no-op if config.yaml is absent)
    notifier_service = NotifierService()
    notifier_service.start(browser_manager)

    # Start Telegram command listener (/list, /add, /delete) if configured
    if notifier_service.telegram and notifier_service.config.telegram_chat_id:
        handler = TelegramCommandHandler(
            notifier_service, notifier_service.telegram, notifier_service.config.telegram_chat_id
        )
        command_task = asyncio.create_task(handler.run(), name="telegram-commands")

    yield

    # Shutdown: stop command listener + notifier loops, then clean up browser resources
    if command_task:
        command_task.cancel()
        try:
            await command_task
        except asyncio.CancelledError:
            pass
    if notifier_service:
        await notifier_service.stop()
    if browser_manager:
        await browser_manager.close()


app = FastAPI(version="1.0.0", lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "message": "Welcome to the Kleinanzeigen API",
        "endpoints": ["/inserate", "/inserat/{id}", "/inserate-detailed"],
        "status": "operational",
    }


app.include_router(inserate.router)
app.include_router(inserat.router)
app.include_router(inserate_detailed.router)
app.include_router(inserate_batch.router)
app.include_router(convert_url.router)
app.include_router(inserate_by_url.router)
