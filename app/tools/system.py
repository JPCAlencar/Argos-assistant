import asyncio
import platform

import psutil
from pydantic import Field

from app.tools.registry import Arguments, NoArguments, Tool, ToolRegistry


class ProcessArguments(Arguments):
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=50, ge=1, le=200)


def register_system(registry: ToolRegistry) -> None:
    async def status(args):
        def work():
            memory = psutil.virtual_memory()
            return {
                "os": platform.system(),
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": memory.percent,
                "available_memory_mb": memory.available // 1048576,
                "confirmed": True,
            }

        return await asyncio.to_thread(work)

    async def processes(args: ProcessArguments):
        def work():
            rows = []
            for process in psutil.process_iter(["pid", "name"]):
                try:
                    rows.append(process.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            rows.sort(key=lambda row: row["pid"])
            return {
                "processes": rows[args.offset : args.offset + args.limit],
                "total": len(rows),
                "confirmed": True,
            }

        return await asyncio.to_thread(work)

    registry.register(
        Tool("get_system_status", "Consulta CPU, memória e sistema operacional.", NoArguments, status)
    )
    registry.register(
        Tool(
            "list_processes",
            "Lista nomes e PIDs; não lê argumentos ou variáveis de ambiente.",
            ProcessArguments,
            processes,
        )
    )
