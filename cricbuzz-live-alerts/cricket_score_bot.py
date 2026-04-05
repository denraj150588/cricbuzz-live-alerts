import asyncio
import time
import os
import pyautogui as gui
from playwright.async_api import async_playwright

# ── CONFIG ────────────────────────────────────────────────────────────────────
CHECK_INTERVAL  = 30             # Seconds between score updates
HEADLESS        = False          # Set True to run without visible browser
SCREENSHOT_DIR  = "screenshots"  # Folder for saved screenshots
DEBUG           = True           # Set True to print raw scraped text
CRICBUZZ_URL    = "https://www.cricbuzz.com"
LIVE_URL        = "https://www.cricbuzz.com/cricket-match/live-scores"
# ─────────────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
#  1.  INPUT COLLECTOR
# ══════════════════════════════════════════════════════════════════════════════

def collect_inputs() -> dict:
    """Collect match preferences from user."""
    print("\n╔══════════════════════════════════════════╗")
    print("║   Cricket Score Alert Bot                ║")
    print("║   Playwright + PyAutoGUI                 ║")
    print("╚══════════════════════════════════════════╝\n")

    print("  Alert me on:")
    print("  1. Any live match")
    print("  2. Specific team  (e.g. India, MI, CSK)")
    print("  3. Specific match type (Test / ODI / T20 / IPL)\n")

    choice = input("  Enter choice (1/2/3) : ").strip()

    inputs = {
        "choice"          : choice,
        "team"            : "",
        "match_type"      : "",
        "alert_wicket"    : False,
        "alert_boundary"  : False,
        "alert_milestone" : False,
        "interval"        : CHECK_INTERVAL,
        "popup_interval"  : 150        # ← NEW: seconds between score popups
    }

    if choice == "2":
        inputs["team"] = input(
            "  Enter team name (e.g. India, MI, CSK, RCB) : "
        ).strip()
    elif choice == "3":
        print("  Match types: Test | ODI | T20 | T20I | IPL")
        inputs["match_type"] = input(
            "  Enter match type : "
        ).strip().upper()

    print("\n  ── Alert Preferences ────────────────────")
    inputs["alert_wicket"]    = input("  Alert on Wickets?    (y/n) : ").strip().lower() == "y"
    inputs["alert_boundary"]  = input("  Alert on Boundaries? (y/n) : ").strip().lower() == "y"
    inputs["alert_milestone"] = input("  Alert on Milestones? (y/n) : ").strip().lower() == "y"

    interval = input(f"\n  Check every N seconds (default {CHECK_INTERVAL}) : ").strip()
    inputs["interval"] = int(interval) if interval.isdigit() else CHECK_INTERVAL

    # ── FIX 2: Ask user how often to show the score popup ────────────────────
    popup_interval = input(
        f"  Show score popup every N seconds (default 150) : "
    ).strip()
    inputs["popup_interval"] = int(popup_interval) if popup_interval.isdigit() else 150

    print("\n  ── Summary ──────────────────────────────")
    print(f"  Watching  : {'Any match' if choice == '1' else inputs.get('team') or inputs.get('match_type')}")
    print(f"  Wickets   : {'Yes' if inputs['alert_wicket'] else 'No'}")
    print(f"  Boundaries: {'Yes' if inputs['alert_boundary'] else 'No'}")
    print(f"  Milestones: {'Yes' if inputs['alert_milestone'] else 'No'}")
    print(f"  Interval  : every {inputs['interval']}s")
    print(f"  Score Popup: every {inputs['popup_interval']}s")   # ← NEW
    print("  ─────────────────────────────────────────\n")

    confirm = input("  Start watching? (y/n) : ").strip().lower()
    if confirm != "y":
        print("  Exiting. Goodbye!")
        exit()

    return inputs


# ══════════════════════════════════════════════════════════════════════════════
#  2.  PLAYWRIGHT  –  scrape live scores
# ══════════════════════════════════════════════════════════════════════════════

async def get_live_matches(page) -> list:
    """
    Scrape all live matches from Cricbuzz using wide selectors
    and raw text fallback. Returns list of match dicts.
    """
    matches = []

    try:
        print(f"\n[Playwright] Loading live scores page...")
        await page.goto(LIVE_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(4_000)

        # ── DEBUG: print raw page text to see what is available ───────────────
        if DEBUG:
            try:
                raw = await page.inner_text("body")
                lines = [l.strip() for l in raw.split("\n") if l.strip()]
                print("\n[DEBUG] Raw page text (first 60 lines):")
                for i, line in enumerate(lines[:60]):
                    print(f"  {i:>3}. {line}")
                print("[DEBUG] End of raw text preview\n")
            except Exception:
                pass

        # ── Strategy 1: known Cricbuzz card selectors ─────────────────────────
        card_selectors = [
            ".cb-lv-scrs-well",
            ".cb-mtch-lst",
            ".cb-scrs-wrp",
            "[class*='cb-lv-scrs']",
            "[class*='match-wrap']",
            "[class*='live-match']",
            "div.cb-col-100.cb-col",
        ]

        match_cards = []
        for sel in card_selectors:
            try:
                cards = await page.query_selector_all(sel)
                if cards:
                    match_cards = cards
                    print(f"[Playwright] Selector '{sel}' → {len(cards)} cards.")
                    break
            except Exception:
                continue

        # ── Strategy 2: grab ALL divs and filter by content ───────────────────
        if not match_cards:
            print("[Playwright] No cards via selectors. Trying broad div scan...")
            match_cards = await page.query_selector_all("div")

        print(f"[Playwright] Processing {len(match_cards)} elements...")

        seen_teams = set()

        for card in match_cards:
            try:
                full_text = (await card.inner_text()).strip()

                # ── Filter: must look like a match card ───────────────────────
                if not full_text or len(full_text) < 10:
                    continue

                # Must contain vs / v / score-like pattern
                text_upper = full_text.upper()
                has_vs     = " VS " in text_upper or " V " in text_upper
                has_score  = any(c.isdigit() for c in full_text)

                if not (has_vs and has_score):
                    continue

                match = {
                    "teams"       : "N/A",
                    "score1"      : "N/A",
                    "score2"      : "N/A",
                    "status"      : "N/A",
                    "match_type"  : "N/A",
                    "crr"         : "N/A",
                    "recent_balls": "N/A",
                    "match_url"   : ""
                }

                lines = [l.strip() for l in full_text.split("\n") if l.strip()]

                # ── Teams: find the line with VS / V ──────────────────────────
                for line in lines:
                    if " vs " in line.lower() or " v " in line.lower():
                        match["teams"] = line
                        break

                # Skip duplicate team entries
                if match["teams"] in seen_teams:
                    continue
                if match["teams"] != "N/A":
                    seen_teams.add(match["teams"])

                # ── Score: find lines with digit/wicket pattern ───────────────
                score_lines = []
                for line in lines:
                    if any(c.isdigit() for c in line) and (
                        "/" in line or "(" in line or
                        line.replace(" ", "").isdigit()
                    ):
                        score_lines.append(line)

                if len(score_lines) >= 1:
                    match["score1"] = score_lines[0]
                if len(score_lines) >= 2:
                    match["score2"] = score_lines[1]

                # ── Status: last meaningful line ──────────────────────────────
                for line in reversed(lines):
                    if len(line) > 5 and not any(
                        c.isdigit() for c in line[:3]
                    ):
                        match["status"] = line
                        break

                # ── Match type: look for known keywords ───────────────────────
                type_keywords = [
                    "IPL", "TEST", "ODI", "T20I", "T20",
                    "PREMIER LEAGUE", "SERIES", "CUP", "TROPHY"
                ]
                for line in lines:
                    for kw in type_keywords:
                        if kw in line.upper():
                            match["match_type"] = line
                            break
                    if match["match_type"] != "N/A":
                        break

                # ── CRR ───────────────────────────────────────────────────────
                for line in lines:
                    if "CRR" in line.upper() or "RUN RATE" in line.upper():
                        match["crr"] = line
                        break

                # ── Match URL ─────────────────────────────────────────────────
                try:
                    link_el = await card.query_selector("a")
                    if link_el:
                        href = await link_el.get_attribute("href")
                        if href:
                            match["match_url"] = (
                                href if href.startswith("http")
                                else CRICBUZZ_URL + href
                            )
                except Exception:
                    pass

                if DEBUG:
                    print(f"[DEBUG] Match found → {match['teams']} | "
                          f"{match['score1']} | {match['status'][:40]}")

                matches.append(match)

            except Exception as e:
                if DEBUG:
                    print(f"[DEBUG] Card error: {e}")
                continue

    except Exception as e:
        print(f"[Playwright] get_live_matches error: {e}")

    return matches


async def get_match_detail(page, match_url: str) -> dict:
    """
    Scrape detailed scorecard from a specific match page.
    Returns detailed match data.
    """
    detail = {
        "batting"     : [],
        "bowling"     : [],
        "last_wicket" : "N/A",
        "partnership" : "N/A",
        "recent_overs": "N/A",
        "toss"        : "N/A",
        "commentary"  : []
    }

    try:
        print(f"[Playwright] Loading match detail: {match_url}")
        await page.goto(match_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3_000)

        # ── Batting scorecard ─────────────────────────────────────────────────
        try:
            bat_rows = await page.query_selector_all(
                ".cb-col.cb-col-100.cb-scrd-itms"
            )
            for row in bat_rows[:6]:
                txt = (await row.inner_text()).strip()
                if txt and "DNB" not in txt:
                    detail["batting"].append(txt)
        except Exception:
            pass

        # ── Bowling scorecard ─────────────────────────────────────────────────
        try:
            bowl_rows = await page.query_selector_all(
                ".cb-col.cb-col-100.cb-scrd-itms.cb-bwl-itms"
            )
            for row in bowl_rows[:4]:
                txt = (await row.inner_text()).strip()
                if txt:
                    detail["bowling"].append(txt)
        except Exception:
            pass

        # ── Last wicket ───────────────────────────────────────────────────────
        try:
            wkt_el = await page.query_selector(
                "[class*='last-wkt'], .cb-lv-lgnd-col"
            )
            if wkt_el:
                detail["last_wicket"] = (await wkt_el.inner_text()).strip()
        except Exception:
            pass

        # ── Partnership ───────────────────────────────────────────────────────
        try:
            partner_el = await page.query_selector("[class*='partnership']")
            if partner_el:
                detail["partnership"] = (await partner_el.inner_text()).strip()
        except Exception:
            pass

        # ── Recent overs ──────────────────────────────────────────────────────
        try:
            overs_el = await page.query_selector(
                ".cb-lv-scrs-col.cb-lv-scrs-col-2, [class*='recent-over']"
            )
            if overs_el:
                detail["recent_overs"] = (await overs_el.inner_text()).strip()
        except Exception:
            pass

        # ── Toss ──────────────────────────────────────────────────────────────
        try:
            toss_el = await page.query_selector(
                "[class*='toss'], .cb-lv-scrs-well"
            )
            if toss_el:
                detail["toss"] = (await toss_el.inner_text()).strip()[:100]
        except Exception:
            pass

        # ── Commentary ────────────────────────────────────────────────────────
        try:
            comm_els = await page.query_selector_all(
                ".cb-col.cb-col-90.cb-com-ln, [class*='commentary']"
            )
            for el in comm_els[:5]:
                txt = (await el.inner_text()).strip()
                if txt:
                    detail["commentary"].append(txt)
        except Exception:
            pass

        # ── Raw text fallback for batting/bowling ─────────────────────────────
        if not detail["batting"] and not detail["bowling"]:
            try:
                raw = await page.inner_text("body")
                lines = [l.strip() for l in raw.split("\n") if l.strip()]
                for line in lines[:80]:
                    if any(c.isdigit() for c in line) and len(line) > 5:
                        detail["batting"].append(line)
                    if len(detail["batting"]) >= 6:
                        break
            except Exception:
                pass

    except Exception as e:
        print(f"[Playwright] get_match_detail error: {e}")

    return detail


async def take_screenshot(page, label: str) -> str:
    """Take a screenshot and return the path."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = f"{SCREENSHOT_DIR}/cricket_{label}_{int(time.time())}.png"
    await page.screenshot(path=path, full_page=False)
    print(f"[Screenshot] Saved → {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════════
#  3.  ALERT DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

def detect_events(old_match: dict, new_match: dict, inputs: dict) -> list:
    """Compare old and new match to detect wickets, fours, sixes, milestones."""
    events    = []
    old_score = old_match.get("score1", "")
    new_score = new_match.get("score1", "")

    if old_score == new_score:
        return events

    def parse_score(score_str: str):
        try:
            parts = score_str.strip().split("/")
            runs  = int(parts[0].strip()) if parts[0].strip().isdigit() else 0
            wkts  = int(parts[1].split()[0]) if len(parts) > 1 else 0
            return runs, wkts
        except Exception:
            return 0, 0

    old_runs, old_wkts = parse_score(old_score)
    new_runs, new_wkts = parse_score(new_score)
    run_diff           = new_runs - old_runs

    if inputs["alert_wicket"] and new_wkts > old_wkts:
        wkts_fallen = new_wkts - old_wkts
        events.append(
            f"🎳  {'WICKETS' if wkts_fallen > 1 else 'WICKET'} FALLEN!\n"
            f"   Score: {new_score}\n"
            f"   Total wickets: {new_wkts}"
        )

    if inputs["alert_boundary"]:
        if run_diff == 4:
            events.append(f"🏏  FOUR! Boundary scored!\n   Score: {new_score}")
        elif run_diff == 6:
            events.append(f"💥  SIX! Maximum scored!\n   Score: {new_score}")

    if inputs["alert_milestone"]:
        for m in [50, 100, 150, 200, 250, 300, 350, 400, 450, 500]:
            if old_runs < m <= new_runs:
                events.append(
                    f"🏆  MILESTONE! Team reached {m} runs!\n"
                    f"   Score: {new_score}"
                )

    return events


def filter_matches(matches: list, inputs: dict) -> list:
    """
    Filter matches based on user preference.
    Uses loose substring matching so IPL teams like MI, CSK, RCB
    and tournament names like IPL are all found correctly.
    """
    # Choice 1 → return everything
    if inputs["choice"] == "1":
        return matches

    filtered = []
    for m in matches:
        # Build a combined text blob from all match fields
        text = " ".join([
            m.get("teams", ""),
            m.get("match_type", ""),
            m.get("status", ""),
            m.get("score1", ""),
        ]).upper()

        if inputs["choice"] == "2":
            # Team filter — loose match, handles MI, CSK, RCB, India, etc.
            if inputs["team"].upper() in text:
                filtered.append(m)

        elif inputs["choice"] == "3":
            # Match type filter — IPL, T20, ODI, TEST
            keyword = inputs["match_type"].upper()
            # Special case: IPL also appears in team names / series names
            if keyword == "IPL" and "IPL" in text:
                filtered.append(m)
            elif keyword in text:
                filtered.append(m)

    if DEBUG and not filtered:
        print(f"[DEBUG] Filter '{inputs.get('team') or inputs.get('match_type')}' "
              f"found no matches in:")
        for m in matches:
            print(f"  → teams='{m['teams']}' type='{m['match_type']}'")

    return filtered


# ══════════════════════════════════════════════════════════════════════════════
#  4.  PYAUTOGUI  –  popup notifications
# ══════════════════════════════════════════════════════════════════════════════

def now_str() -> str:
    """Return current date and time as a formatted string."""
    return time.strftime('%d %b %Y  %H:%M:%S')   # ← FIX 1: full date + time


def show_start_popup(inputs: dict):
    watch_label = (
        "All live matches" if inputs["choice"] == "1"
        else inputs.get("team") or inputs.get("match_type")
    )
    message = (
        f"🏏  Cricket Score Bot Started!\n"
        f"{'─' * 45}\n"
        f"Watching    : {watch_label}\n"
        f"Wickets     : {'✅ Yes' if inputs['alert_wicket'] else '❌ No'}\n"
        f"Boundaries  : {'✅ Yes' if inputs['alert_boundary'] else '❌ No'}\n"
        f"Milestones  : {'✅ Yes' if inputs['alert_milestone'] else '❌ No'}\n"
        f"{'─' * 45}\n"
        f"Check Interval : every {inputs['interval']}s\n"
        f"Score Popup    : every {inputs['popup_interval']}s\n"   # ← NEW
        f"{'─' * 45}\n"
        f"Started at  : {now_str()}\n"                           # ← FIX 1
        f"Popup alerts will appear for selected events!"
    )
    gui.alert(text=message, title="Cricket Bot — Started", button="OK")


def show_live_scores_popup(matches: list, screenshot: str):
    if not matches:
        gui.alert(
            text="No live matches found at the moment.\nCheck back later!",
            title="Cricket Bot — No Live Matches",
            button="OK"
        )
        return

    lines = [f"🏏  LIVE SCORES  ({now_str()})\n{'─' * 45}"]   # ← FIX 1

    for i, m in enumerate(matches, 1):
        lines.append(
            f"\n  Match {i}: {m['teams']}\n"
            f"  Type   : {m['match_type']}\n"
            f"  Score  : {m['score1']}"
            + (f"  |  {m['score2']}" if m['score2'] != "N/A" else "") + "\n"
            f"  Status : {m['status']}\n"
            + (f"  CRR    : {m['crr']}\n" if m['crr'] != "N/A" else "")
            + f"  {'─' * 43}"
        )

    lines.append(f"\nScreenshot: {screenshot}")
    gui.alert(
        text="\n".join(lines),
        title=f"Cricket Bot — {len(matches)} Live Match(es)",
        button="OK"
    )


def show_score_update_popup(match: dict, detail: dict):
    batting_summary = (
        "\n".join(f"    {b}" for b in detail["batting"][:3]) or "  N/A"
    )
    bowling_summary = (
        "\n".join(f"    {b}" for b in detail["bowling"][:2]) or "  N/A"
    )
    commentary = (
        "\n".join(f"    • {c[:80]}" for c in detail["commentary"][:3]) or "  N/A"
    )
    message = (
        f"🏏  SCORE UPDATE  ({now_str()})\n"                    # ← FIX 1
        f"{'─' * 45}\n"
        f"Match  : {match['teams']}\n"
        f"Type   : {match['match_type']}\n"
        f"{'─' * 45}\n"
        f"Score  : {match['score1']}"
        + (f"  |  {match['score2']}" if match['score2'] != "N/A" else "") + "\n"
        f"Status : {match['status']}\n"
        + (f"CRR    : {match['crr']}\n" if match['crr'] != "N/A" else "")
        + f"{'─' * 45}\n"
        f"Batting:\n{batting_summary}\n\n"
        f"Bowling:\n{bowling_summary}\n\n"
        f"{'─' * 45}\n"
        f"Last Wicket  : {detail['last_wicket']}\n"
        f"Partnership  : {detail['partnership']}\n"
        f"Recent Overs : {detail['recent_overs']}\n"
        f"{'─' * 45}\n"
        f"Commentary:\n{commentary}"
    )
    gui.alert(text=message, title=f"Cricket — {match['teams']}", button="OK")


def show_event_popup(match: dict, event: str):
    message = (
        f"{event}\n"
        f"{'─' * 45}\n"
        f"Match  : {match['teams']}\n"
        f"Type   : {match['match_type']}\n"
        f"Status : {match['status']}\n"
        f"Time   : {now_str()}"                                  # ← FIX 1
    )
    gui.alert(
        text=message,
        title=f"Cricket Alert — {match['teams']}",
        button="OK"
    )


def show_match_ended_popup(match: dict):
    message = (
        f"🏁  MATCH ENDED!\n"
        f"{'─' * 45}\n"
        f"Match  : {match['teams']}\n"
        f"Type   : {match['match_type']}\n"
        f"Result : {match['status']}\n"
        f"Score  : {match['score1']}"
        + (f"  |  {match['score2']}" if match['score2'] != "N/A" else "") + "\n"
        f"{'─' * 45}\n"
        f"Time   : {now_str()}"                                  # ← FIX 1
    )
    gui.alert(text=message, title="Cricket Bot — Match Ended", button="OK")


def show_no_matches_popup():
    gui.alert(
        text=(
            "😴  No live matches found right now.\n\n"
            "The bot will keep watching and alert\n"
            "you the moment a match goes live!"
        ),
        title="Cricket Bot — Waiting for Match",
        button="OK"
    )


def show_error_popup(error: str):
    gui.alert(
        text=f"❌  ERROR\n{'─' * 40}\n{error}",
        title="Cricket Bot — Error",
        button="OK"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  5.  WATCHER LOOP
# ══════════════════════════════════════════════════════════════════════════════

async def watch_cricket(inputs: dict):
    """
    Main watcher loop. Single tab only.
    Navigates back to LIVE_URL after each detail scrape.
    """
    check_count    = 0
    previous_data  = {}
    ended_matches  = set()
    last_popup_time = 0   # ← FIX 3: track last score popup time in seconds

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=["--start-maximized"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            no_viewport=True
        )

        # ── Single tab only ───────────────────────────────────────────────────
        page = await context.new_page()

        print("\n[Watcher] Cricket Score Bot running...")
        print(f"[Watcher] Checking every {inputs['interval']} seconds.")
        print(f"[Watcher] Score popup every {inputs['popup_interval']} seconds.\n")

        while True:
            check_count += 1
            print(f"\n[Watcher] ── Check #{check_count} at {now_str()} ──")  # ← FIX 1

            try:
                all_matches = await get_live_matches(page)
                matches     = filter_matches(all_matches, inputs)

                print(f"[Watcher] Total scraped: {len(all_matches)} | "
                      f"After filter: {len(matches)}")

                if not matches:
                    print("[Watcher] No matching live matches found.")
                    if check_count == 1:
                        show_no_matches_popup()
                    await asyncio.sleep(inputs["interval"])
                    continue

                # ── First check → show all live scores ────────────────────────
                if check_count == 1:
                    shot = await take_screenshot(page, "live_scores")
                    show_live_scores_popup(matches, shot)

                # ── Process each match ────────────────────────────────────────
                for match in matches:
                    key          = match["teams"]
                    status_upper = match["status"].upper()

                    # ── Match ended ───────────────────────────────────────────
                    if any(w in status_upper for w in
                           ["WON", "DRAW", "TIED", "ABANDONED", "RESULT"]):
                        if key not in ended_matches:
                            ended_matches.add(key)
                            print(f"[Watcher] Match ended: {key}")
                            show_match_ended_popup(match)
                        continue

                    # ── Detect events ─────────────────────────────────────────
                    if key in previous_data:
                        events = detect_events(previous_data[key], match, inputs)
                        for event in events:
                            print(f"[Watcher] Event: {event[:50]}")
                            show_event_popup(match, event)

                    # ── FIX 3: Show scorecard popup based on elapsed seconds ──
                    now = time.time()
                    if (now - last_popup_time) >= inputs["popup_interval"] \
                            and match.get("match_url"):
                        last_popup_time = now
                        print(f"[Watcher] Popup interval reached → loading detail...")
                        detail = await get_match_detail(page, match["match_url"])
                        show_score_update_popup(match, detail)
                        await take_screenshot(page, key.replace(" ", "_"))

                        # ── Navigate back to live scores ──────────────────────
                        print("[Watcher] Navigating back to live scores...")
                        await page.goto(
                            LIVE_URL,
                            wait_until="domcontentloaded",
                            timeout=30_000
                        )
                        await page.wait_for_timeout(2_000)

                    previous_data[key] = match
                    print(f"[Watcher] {key} → {match['score1']} | "
                          f"{match['status'][:40]}")

            except Exception as e:
                print(f"[Watcher] Error: {e}")
                show_error_popup(str(e))

            await asyncio.sleep(inputs["interval"])


# ══════════════════════════════════════════════════════════════════════════════
#  6.  MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    gui.FAILSAFE = True
    gui.PAUSE    = 0.05

    inputs = collect_inputs()
    show_start_popup(inputs)

    try:
        await watch_cricket(inputs)
    except KeyboardInterrupt:
        print("\n[Watcher] Stopped by user.")
        gui.alert(
            text=(
                "Cricket Score Bot stopped.\n"
                "Move mouse to top-left corner to abort anytime."
            ),
            title="Cricket Bot — Stopped",
            button="OK"
        )
    except Exception as e:
        print(f"\n[Watcher] Fatal error: {e}")
        show_error_popup(str(e))


if __name__ == "__main__":
    asyncio.run(main())
