import os
from playwright.sync_api import expect, sync_playwright

from enhancement_fixture import DEMO_PASSWORD, running_server


with running_server() as (base, _), sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"))
    page = browser.new_page(accept_downloads=True,viewport={"width":1280,"height":850})
    errors = []
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.on("dialog",lambda dialog:dialog.accept())

    def login(user,password):
        page.locator("#u").fill(user)
        page.locator("#p").fill(password)
        page.get_by_role("button",name="Login",exact=True).click()
        expect(page.get_by_role("navigation")).to_be_visible()

    page.goto(base)
    page.get_by_role("button",name="Request access",exact=True).click()
    page.get_by_label("User ID").fill("new.employee")
    page.get_by_label("Full name").fill("New Employee")
    page.get_by_role("button",name="Send request").click()
    expect(page.get_by_role("status")).to_contain_text("Request received")
    page.get_by_role("button",name="Back to sign in").click()
    login("A1",DEMO_PASSWORD)
    page.get_by_role("button",name="Account requests",exact=True).click()
    page.get_by_role("button",name="Approve",exact=True).click()
    code = page.locator("#secondary code").inner_text()
    page.goto(base)
    page.get_by_role("button",name="Use account code",exact=True).click()
    page.get_by_label("One-time account code").fill(code)
    password = "Browser-test-password!45"
    page.get_by_label("New password").fill(password)
    page.get_by_role("button",name="Set password").click()
    expect(page.get_by_role("status")).to_contain_text("Password set")
    login("new.employee",password)

    page.get_by_role("button",name="Triage assistant").click()
    page.get_by_label("Issue or follow-up").fill("Users are locked out at login")
    page.get_by_role("button",name="Get suggestions").click()
    expect(page.locator("#triage-result")).to_contain_text("AUTHENTICATION")
    page.get_by_role("button",name="My analytics").click()
    expect(page.get_by_role("button",name="Open incidents")).to_be_visible()
    expect(page.get_by_role("button",name="Export CSV")).to_have_count(0)
    page.get_by_role("button",name="My Complaints",exact=True).click()
    expect(page.get_by_role("button",name="Export CSV")).to_have_count(0)
    page.goto(base)
    login("A1",DEMO_PASSWORD)
    with page.expect_download() as finished:
        page.get_by_role("button",name="Export CSV").click()
    download = finished.value
    assert download.suggested_filename == "incidents.csv"
    assert download.path().read_text().startswith("ID,Title,Service,Category")
    assert not errors, errors
    browser.close()
    print("PASS browser access review, one-time password setup, triage, scoped analytics and CSV download")
