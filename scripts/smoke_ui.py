"""Validação real do painel local com Chromium; não consulta IA nem controla aplicativos."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parent.parent


async def main():
    lines = (ROOT / ".runtime" / "first-access.txt").read_text(encoding="utf-8").splitlines()
    password = next(line.split(": ", 1)[1] for line in lines if line.startswith("Senha: "))
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, locale="pt-BR")
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto("http://127.0.0.1:8000")
        await page.locator("#login-view").wait_for(state="visible")
        await page.screenshot(path=str(output / "login.png"), full_page=True)
        await page.locator('input[name="password"]').fill(password)
        await page.locator("#login-form button").click()
        await page.locator("#shell").wait_for(state="visible")
        await page.locator(".hero").wait_for()
        await page.screenshot(path=str(output / "dashboard-desktop.png"), full_page=True)
        for route in ("provider", "tools", "devices", "memory", "security", "settings", "tasks"):
            await page.locator('[data-page="' + route + '"]').click()
            await page.wait_for_function(
                "(expected) => document.querySelector('#page-name').textContent === expected",
                arg={
                    "provider": "Modelos",
                    "tools": "Ferramentas",
                    "devices": "Dispositivos",
                    "memory": "Memória",
                    "security": "Segurança",
                    "settings": "Configurações",
                    "tasks": "Histórico",
                }[route],
            )
            await page.locator("#content .card").first.wait_for()
        await page.locator('[data-page="workflows"]').click()
        await page.locator("[data-workflow]").first.wait_for()
        await page.locator("[data-workflow]").first.click()
        await page.locator("#task-status").wait_for()
        await page.wait_for_function(
            "() => document.querySelector('#task-status')?.textContent.includes('Concluída')", timeout=30000
        )
        assert await page.locator("#messages").inner_text() != ""
        await page.screenshot(path=str(output / "workflow-chat.png"), full_page=True)
        await page.locator('[data-page="chat"]').click()
        await page.locator("#new-conversation").click()
        await page.locator(".chat-welcome").wait_for()
        await page.locator("#objective").fill("Olá, Argos. O que você consegue fazer?")
        await page.locator("#send-message").click()
        await page.wait_for_function(
            "() => document.querySelector('#task-status')?.textContent.includes('Falhou')", timeout=30000
        )
        await expect(page.locator("#messages")).to_contain_text("Cadastre sua API key")
        await page.locator('[data-page="dashboard"]').click()
        await page.locator(".hero").wait_for()
        await page.set_viewport_size({"width": 390, "height": 844})
        await expect(page.locator("#sidebar")).not_to_be_in_viewport()
        await page.screenshot(path=str(output / "dashboard-mobile.png"), full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.locator("#menu-button").click()
        await page.locator('[data-page="chat"]').click()
        await page.locator("#objective").wait_for()
        await expect(page.locator("#sidebar")).not_to_be_in_viewport()
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.screenshot(path=str(output / "chat-mobile.png"), full_page=True)
        await browser.close()
    assert not errors, errors
    summary = {
        "browser": "Chromium headless",
        "checks": [
            "login real",
            "navegação nas telas",
            "workflow real sem IA",
            "SSE e conclusão",
            "erro claro sem API key",
            "dashboard e chat em 390px",
            "sem erros JavaScript",
        ],
        "errors": errors,
    }
    (output / "ui-smoke.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Smoke UI: 7 verificações aprovadas; capturas em test-results/. Nenhuma chamada paga.")


if __name__ == "__main__":
    asyncio.run(main())
