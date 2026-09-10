#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
梵蒂冈余票监测 - 云端单次版 (供 GitHub Actions 定时调用)
========================================================

与本地版的区别：
    - 本地版是 while True 一直循环；云端版【只检查一次就退出】，
      由 GitHub Actions 的定时任务(cron)每隔几分钟自动来跑一次。
    - 检测到票时，通过 PushPlus 把消息【推送到你的微信】。
    - 强制无头 + 自带 Chromium（云端没有你的 Edge）。

判断依据同本地版：预订按钮是否 disabled（不受语言 BOOK/PRENOTA 影响）。
只监测你指定的两种票(意语页面)。

需要的环境变量(在 GitHub Secrets 里配置)：
    PUSHPLUS_TOKEN  -> 你的 PushPlus token（微信推送用）

它【只监测提醒，不下单/不付款】。收到微信后你自己打开官网手动买(选2人、填实名、付款)。
"""

import os
import sys
import time
import datetime

import requests
from playwright.sync_api import sync_playwright

# ==================== 配置区 ====================

TARGET_DATE = datetime.date(2026, 10, 2)   # 你要的日期
NUM_TICKETS = 2                            # 人数(仅提醒用)

# 只监测这两种票，用意语标题关键词识别
WANTED_CARDS = {
    "普通入场票 (Biglietti d'ingresso)":        "ingresso",
    "散客导览票 (Visite Guidate Singoli Musei)": "Visite Guidate Singoli",
}

BOOKING_HOME = "https://tickets.museivaticani.va/home"
AVAILABILITY_BASE = "https://tickets.museivaticani.va/home/fromtag/2"

# PushPlus token 从环境变量读取（GitHub Secrets），本地测试也可直接填这里
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")

# ===============================================


def target_epoch_millis(d: datetime.date) -> int:
    dt = datetime.datetime(d.year, d.month, d.day, 0, 0, 0)
    return int(time.mktime(dt.timetuple()) * 1000)


def availability_url(d: datetime.date) -> str:
    return f"{AVAILABILITY_BASE}/{target_epoch_millis(d)}/MV-Biglietti/1"


def push_wechat(title: str, content: str):
    """通过 PushPlus 推送到微信。"""
    if not PUSHPLUS_TOKEN:
        print("  [!] 未配置 PUSHPLUS_TOKEN，跳过微信推送。")
        return
    try:
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": PUSHPLUS_TOKEN,
                "title": title,
                "content": content,
                "template": "html",
            },
            timeout=15,
        )
        print(f"  [i] 微信推送结果: {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"  [!] 微信推送失败: {e}")


def check_tickets(page):
    """逐张卡片判断，核心=按钮是否 disabled。返回 (是否有票, 状态列表)。"""
    results = []
    available = False

    footers = page.locator("div.muvaTicketFooterContainer")
    n = footers.count()
    found = {}

    for i in range(n):
        footer = footers.nth(i)
        btn = footer.locator("div.bookButtonDiv button")
        if btn.count() == 0:
            continue
        try:
            is_disabled = btn.first.is_disabled()
            btn_text = (btn.first.inner_text() or "").strip()
        except Exception:
            continue

        # 找恰好只含一个标题的最近祖先 = 单张卡片边界，保证标题与按钮一一对应
        try:
            title_text = footer.evaluate(
                """el => {
                    let p = el; let lastGood = '';
                    for (let k = 0; k < 10 && p.parentElement; k++) {
                        p = p.parentElement;
                        const titles = p.querySelectorAll('.muvaTicketTitle');
                        if (titles.length === 1) { lastGood = titles[0].innerText || ''; }
                        else if (titles.length > 1) { break; }
                    }
                    return lastGood;
                }"""
            ) or ""
        except Exception:
            title_text = ""

        for label, keyword in WANTED_CARDS.items():
            if keyword.lower() in title_text.lower():
                is_avail = (not is_disabled)
                prev = found.get(keyword)
                if prev is None or (is_avail and not prev[1]):
                    found[keyword] = (label, is_avail, btn_text)

    for label, keyword in WANTED_CARDS.items():
        if keyword not in found:
            results.append(f"⚠ 未找到 {label}")
            continue
        _, is_avail, btn_text = found[keyword]
        if is_avail:
            results.append(f"✅ {label} → 有票！按钮：[{btn_text}]")
            available = True
        else:
            results.append(f"❌ {label} → 无票（[{btn_text}]）")

    return available, results


def main():
    url = availability_url(TARGET_DATE)
    print(f"[{datetime.datetime.now()}] 检查 {TARGET_DATE} 余票")
    print(f"  URL: {url}")

    available = False
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="it-IT",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_selector("div.bookButtonDiv button", timeout=20000)
            except Exception:
                page.wait_for_timeout(3000)

            body_lower = page.inner_text("body").lower()
            if ("verify you are human" in body_lower or "turnstile" in body_lower
                    or "checking your browser" in body_lower):
                print("  [!] 遇到官网安全验证，云端无法手动通过，本次跳过。")
                context.close(); browser.close()
                return

            available, results = check_tickets(page)
            for r in results:
                print("     " + r)
        except Exception as e:
            print(f"  [!] 检查异常: {e}")
        finally:
            try:
                context.close(); browser.close()
            except Exception:
                pass

    if True:
        detail = "<br>".join(results)
        push_wechat(
            "🎫 梵蒂冈有票了！",
            f"<b>{TARGET_DATE.isoformat()} 你要的票可预订！</b><br><br>"
            f"{detail}<br><br>"
            f"👉 立刻打开官网下单，选 {NUM_TICKETS} 位参加者、填两人实名：<br>"
            f"<a href='{BOOKING_HOME}'>{BOOKING_HOME}</a>",
        )
        print(">>> 检测到有票，已尝试微信推送。")
    else:
        print(">>> 本次无票。")


if __name__ == "__main__":
    main()
