from enhancement_fixture import DEMO_PASSWORD, wait_for_server
import os, subprocess, time, sys
from playwright.sync_api import sync_playwright
BASE_URL = "http://127.0.0.1:" + os.getenv("RESOLV_PORT", "8000")
os.environ["RESOLV_DEMO_PASSWORD"] = DEMO_PASSWORD
P = subprocess.Popen([sys.executable, "server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
errs, dialogs = [], []
try:
    wait_for_server(BASE_URL, P)
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")); pg = b.new_page(viewport={"width": 390, "height": 800})
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("response", lambda r: errs.append(f"{r.status} {r.url}") if "/api/" in r.url and r.status >= 400 else None)
        answers = []
        def on_dialog(d):
            dialogs.append((d.type, d.message[:70]))
            d.accept(answers.pop(0) if d.type == "prompt" and answers else d.default_value)
        pg.on("dialog", on_dialog)
        def login(u, pw=DEMO_PASSWORD):
            pg.goto(BASE_URL + "/"); pg.fill("#u", u); pg.fill("#p", pw); pg.click("text=Login"); pg.wait_for_selector(".workspace-top")
        def tab(t):
            if pg.get_by_role("button",name="Open menu").is_visible(): pg.get_by_role("button",name="Open menu").click()
            pg.locator("nav").get_by_role("button",name=t,exact=True).click()
            time.sleep(.3)
        def btn(t): pg.click(f"button:has-text('{t}')"); time.sleep(.4)
        def check(label, *conditions):
            assert all(conditions), label
            print("PASS", label)
        def issue():
            pg.get_by_role("button", name="Payment application unavailable", exact=True).click()
            pg.wait_for_selector("#drawer[open]")
        login("E1"); btn("+ Raise Issue")
        pg.fill("#t", "Payment application unavailable"); pg.fill("#d", "users cannot complete transactions"); pg.select_option("#s", "Payment")
        pg.locator('label:has(input[name=impact][value="4"])').click(); pg.locator('label:has(input[name=urgency][value="4"])').click(); pg.fill("#n", "300"); btn("Submit"); pg.wait_for_selector("tbody .badge", timeout=4000)
        check("employee sees:", "P1" in pg.inner_text("#v"), "Assigned" in pg.inner_text("#v"))
        login("T1"); tab("Workload"); check("tech workload:", "1 / 5" in pg.inner_text("#v"))
        issue(); btn("Acknowledge"); btn("Start work"); answers[:] = ["Which region?"]; btn("Request info")
        answers[:] = ["Checked logs, gateway hung"]; btn("Add note"); check("notes shown:", "INFO REQUEST" in pg.inner_text("#drawer"))
        login("E1"); issue(); answers[:] = ["EU region"]; btn("Reply / add info"); check("reply shown:", "REPLY" in pg.inner_text("#drawer"))
        login("T1"); issue(); answers[:] = ["Restarted payment gateway"]; btn("Resolve")
        login("E1"); issue(); answers[:] = ["Thanks!"]; btn("Verify"); check("verified:", "Verified" in pg.inner_text("#v"))
        login("A1"); issue(); pg.get_by_role("button", name="Close", exact=True).click(); pg.get_by_role("button", name="Close drawer").click(); check("closed:", "Closed" in pg.inner_text("#v"))
        tab("Analytics"); check("dashboard:", "total" in pg.inner_text("#v"))
        tab("Audit log"); check("audit:", "CREATED" in pg.inner_text("#v"))
        tab("Admin"); btn("+30 min"); check("clock:", "30 min" in pg.inner_text("#v"))
        pg.fill("#sr", "7"); pg.fill("#ss", "40"); btn("Save SLA"); check("sla ui:", "respond 7" in pg.inner_text("#v"))
        btn("Load-based"); check("strategy:", "LoadBased" in pg.inner_text("#v"))

        print("-- registration + admin user management + filters")
        pg.goto(BASE_URL + "/"); pg.fill("#u", "uiuser1"); btn("Register as employee")
        pg.get_by_label("Full name").fill("UI User"); pg.get_by_label("Password").fill("test-user-password-123"); btn("Create account")
        pg.wait_for_selector("#signin-status:text('Account created')")
        login("uiuser1", "test-user-password-123")
        pg.get_by_role("button",name="Open menu").click()
        pg.locator(".rail-account b").wait_for()
        check("registered user logs in:", pg.locator(".rail-account b").inner_text() == "UI User")
        pg.get_by_role("button",name="Close menu",exact=True).click()
        login("A1"); tab("Users"); pg.fill("#ui", "uitech"); pg.fill("#un", "UI Tech"); pg.fill("#up", "test-user-password-123"); pg.select_option("#ur", "TECHNICIAN"); pg.fill("#us", "NETWORK"); pg.fill("#uc", "3"); btn("Create")
        check("tech created:", "uitech" in pg.inner_text("#v") and "NETWORK" in pg.inner_text("#v"))
        pg.click("div.panel:has-text('uitech') >> button:has-text('Mark unavailable')"); time.sleep(.4); check("availability toggled:", "UNAVAILABLE" in pg.inner_text("div.panel:has-text('uitech')"))
        pg.fill("#cn", "ANALYTICS"); pg.fill("#ck", "analytics,dashboards"); btn("Save"); check("category added:", "ANALYTICS" in pg.inner_text("#v"))
        tab("All Issues"); pg.select_option("select:has(option:text('Any category'))", "APPLICATION"); time.sleep(.4); check("category filter:", "INC1001" in pg.inner_text("#v"))
        pg.select_option("select:has(option:text('Any category'))", "NETWORK"); time.sleep(.4); check("category filter excludes:", "INC1001" not in pg.inner_text("#v"))
        pg.select_option("select:has(option:text('Any category'))", ""); pg.fill("#search", "inc1001"); time.sleep(.4)
        check("id filter:", "INC1001" in pg.inner_text("#v"))
        pg.fill("#search", "application unavailable")
        assert pg.get_by_role("button", name="Payment application unavailable").is_visible()
        pg.fill("#search", "no such issue")
        assert pg.get_by_text("No issues match this view.").is_visible()
        pg.fill("#search", "")
        pg.locator("summary").click()
        pg.fill("input[placeholder^='Incident ID']", "inc1001")
        pg.press("input[placeholder^='Incident ID']", "Tab")
        pg.wait_for_selector("tbody .ticket-link")
        for width in (320, 390, 1280):
            pg.set_viewport_size({"width": width, "height": 800})
            assert pg.evaluate("document.documentElement.scrollWidth === innerWidth"), width
        pg.locator("summary").click()
        pg.click("text=Logout"); time.sleep(.4); check("logout returns to login:", "Sign in" in pg.inner_text("#app"))
        b.close()
finally:
    P.terminate()
    P.wait(timeout=5)
assert not errs, errs
print("JS/API errors:", errs); print("alerts/dialogs:", [d for d in dialogs if d[0] == "alert"])
