from pathlib import Path
import os
import re
import subprocess
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright

from enhancement_fixture import DEMO_PASSWORD, PROJECT, running_server


def login(page, base, uid):
    page.goto(base)
    page.locator("#u").fill(uid)
    page.locator("#p").fill(DEMO_PASSWORD)
    page.get_by_role("button", name="Login", exact=True).click()
    expect(page.locator(".workspace-top")).to_be_visible()


voice_enabled = bool(os.environ.get("RESOLV_VOSK_MODEL"))
with TemporaryDirectory() as tmp, running_server() as (base, _), sync_playwright() as pw:
    options = {"executable_path":os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"), "args":["--use-fake-device-for-media-stream"]}
    if voice_enabled:
        recording = Path(tmp) / "voice.wav"
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",
                        "flite=text='Network outage at Gandhi Bhavan. The network is down.':voice=slt",
                        "-ar","48000","-y",str(recording)], check=True)
        options["args"].append(f"--use-file-for-fake-audio-capture={recording}")
    browser = pw.chromium.launch(**options)
    context = browser.new_context(viewport={"width":390,"height":850}, color_scheme="light", permissions=["microphone"])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error:errors.append(str(error)))
    page.on("dialog", lambda dialog:dialog.accept())
    page.goto(base)
    emblem = page.get_by_role("img",name="BITS Pilani emblem")
    expect(emblem).to_be_visible()
    assert emblem.evaluate("img => img.complete && img.naturalWidth === 130 && img.naturalHeight === 120")
    switch = page.get_by_role("button",name="Dark mode",exact=True)
    switch.focus()
    assert switch.evaluate("button => getComputedStyle(button).outlineStyle") != "none"
    switch.press("Space")
    expect(page.locator("html")).to_have_attribute("data-theme","dark")
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme","dark")
    login(page,base,"E1")
    expect(page.locator("html")).to_have_attribute("data-theme","dark")
    expect(page.get_by_role("img",name="BITS Pilani emblem")).to_be_visible()
    page.get_by_role("button",name="+ Raise Issue").click()
    expect(page.locator("#priority-preview")).to_contain_text("Response")
    page.evaluate("""() => {
      window.SpeechRecognition = class {
        start() { this.onresult({results:[[{transcript:'A sentence from dictation'}]]}); }
        abort() {}
      };
    }""")
    page.get_by_role("button",name="Dictate description").click()
    expect(page.locator("#d")).to_have_value("A sentence from dictation")
    page.locator("#d").fill("")

    if voice_enabled:
        page.get_by_role("button",name="Record issue").click()
        expect(page.locator("#voice-status")).to_contain_text("Recording")
        page.get_by_role("button",name="Stop recording").click()
        expect(page.locator("#voice-preview")).to_be_visible()
        page.get_by_role("button",name="Discard recording").click()
        expect(page.locator("#voice-preview")).to_be_hidden()
        page.get_by_role("button",name="Record issue").click()
        expect(page.get_by_role("button",name="Stop recording")).to_be_visible()
        page.wait_for_timeout(4600)
        page.get_by_role("button",name="Stop recording").click()
        expect(page.locator("#voice-preview")).to_be_visible()
        assert page.locator("#voice-preview").evaluate("async audio => { await audio.play(); audio.pause(); return !!audio.src; }")
        page.get_by_role("button",name="Transcribe recording").click()
        expect(page.get_by_label("Transcript",exact=True)).to_have_value(re.compile("network"),timeout=15000)
        page.get_by_label("Transcript",exact=True).fill("Network outage at Gandhi Bhavan. The network is down.")
        page.get_by_role("button",name="Use transcript in report").click()
        expect(page.locator("#s")).to_have_value("Network")
        expect(page.locator("#d")).to_have_value("Network outage at Gandhi Bhavan. The network is down.")
        expect(page.locator("#report-location")).to_contain_text("Gandhi Bhavan")
        expect(page.locator("#voice-preview")).to_be_hidden()
        expect(page.locator("#results")).to_contain_text("No issues match this view.")

    denied = context.new_page()
    denied.add_init_script("""Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
        value() { return Promise.reject(new DOMException('denied', 'NotAllowedError')); }
    });""")
    login(denied,base,"E2")
    denied.get_by_role("button",name="+ Raise Issue").click()
    denied.get_by_role("button",name="Record issue").click()
    expect(denied.locator("#voice-status")).to_contain_text("permission was denied")
    expect(denied.locator("#t")).to_be_editable()
    denied.close()

    page.locator("#t").fill("Intercom outage")
    page.locator("#d").fill("Intercom lost its connection")
    page.locator("#s").select_option("Network")
    page.locator("#report-category").select_option("OTHER")
    expect(page.get_by_label("Describe the Other category")).to_be_visible()
    assert page.get_by_label("Describe the Other category").evaluate("input => input.required")
    page.get_by_label("Describe the Other category").fill("Audio intercom")
    page.get_by_role("button",name="Select campus location").click()
    expect(page.locator(".campus-map .marker-0")).to_have_count(12)
    page.get_by_label("Find a campus area").fill("Gandhi")
    page.get_by_label("Campus area",exact=True).select_option("gandhi-bhavan")
    page.get_by_role("button",name="Pin selected area / map centre").click()
    expect(page.locator(".issue-pin")).to_be_visible()
    page.get_by_role("button",name="Clear pin").click()
    expect(page.locator(".issue-pin")).to_have_count(0)
    page.get_by_role("button",name="Place or move pin").click()
    page.locator(".campus-map").click(position={"x":155,"y":125})
    expect(page.locator(".issue-pin")).to_be_visible()
    page.get_by_role("button",name="Use location").click()
    expect(page.locator("#report-location")).to_contain_text("Gandhi Bhavan · pin")
    page.locator('[data-picker="report"] input').set_input_files([
        {"name":"discard.txt","mimeType":"text/plain","buffer":b"delete"},
        {"name":"evidence.png","mimeType":"image/png","buffer":(PROJECT / "assets/bits-pilani-logo.png").read_bytes()},
    ])
    page.get_by_role("button",name="Remove discard.txt").click()
    page.get_by_role("button",name="Submit issue").click()
    page.get_by_role("button",name="Notifications",exact=True).click()
    expect(page.locator("#alert-popover")).to_contain_text("Assigned")
    page.get_by_role("button",name="Mark all read").click()
    expect(page.locator("#unread-dot")).to_be_hidden()
    page.get_by_role("button",name="Notifications",exact=True).click()
    page.locator("#search").fill("Audio intercom")
    page.get_by_role("button",name="Intercom outage",exact=True).click()
    expect(page.get_by_text("Gandhi Bhavan · pin",exact=True).last).to_be_visible()
    expect(page.get_by_text("discard.txt")).to_have_count(0)
    page.get_by_role("button",name="Preview",exact=True).click()
    expect(page.locator(".evidence-preview")).to_be_visible()
    page.get_by_role("button",name="View campus pin").click()
    expect(page.locator(".issue-pin")).to_be_visible()
    page.locator("#secondary").get_by_role("button",name="Close",exact=True).click()

    login(page,base,"A1")
    page.get_by_role("button",name="Intercom outage",exact=True).click()
    page.get_by_role("button",name="Admin Override",exact=True).click()
    page.get_by_label("Proposed Status").select_option("CLOSED")
    page.get_by_label("Proposed Priority").select_option("P1")
    page.get_by_label("Reason for override").fill("Duplicate service escalation")
    page.get_by_role("button",name="Review and apply override").click()
    expect(page.get_by_text("Override applied.")).to_be_visible()
    expect(page.get_by_text("Admin priority override")).to_be_visible()
    page.get_by_role("button",name="Admin Override",exact=True).click()
    page.get_by_label("Proposed Status").select_option("ASSIGNED")
    page.get_by_label("Reason for override").fill("Independent outage")
    page.get_by_role("button",name="Review and apply override").click()
    expect(page.get_by_text("Override applied.")).to_be_visible()
    page.get_by_role("button",name="Close drawer").click()
    page.get_by_role("button",name="Open menu").click()
    page.get_by_role("button",name="Audit log",exact=True).click()
    item = page.locator("#audit-rows article").filter(has_text="Duplicate service escalation")
    item.get_by_role("button",name="Hide entry").click()
    page.get_by_label("Reason or correction note").fill("Triage complete")
    page.get_by_role("button",name="Confirm hide").click()
    expect(page.locator("#audit-status")).to_contain_text("hidden")
    expect(item.get_by_role("button",name="Hide entry")).to_have_count(0)
    page.get_by_role("button",name="Show hidden records").click()
    item.get_by_role("button",name="Restore entry").click()
    page.get_by_label("Reason or correction note").fill("Restore verified")
    page.get_by_role("button",name="Confirm restore").click()
    expect(page.locator("#audit-status")).to_contain_text("restored")
    page.get_by_role("button",name="Open menu").click()
    page.get_by_role("button",name="Analytics",exact=True).click()
    expect(page.get_by_role("button",name="SLA at risk",exact=False)).to_be_visible()
    page.get_by_text("Detailed analytics",exact=True).click()
    page.get_by_role("button",name="Hourly workload",exact=True).click()
    expect(page.get_by_role("heading",name="Reports by hour")).to_be_visible()
    page.get_by_role("button",name="Services & activity",exact=True).click()
    expect(page.get_by_role("heading",name="Recent activity")).to_be_visible()
    page.get_by_label("Category",exact=True).select_option("OTHER")
    page.get_by_role("button",name="Apply filters",exact=True).click()
    page.get_by_role("button",name="Campus heat map",exact=True).click()
    page.locator("#heat-map").get_by_role("button",name="Gandhi Bhavan (1)",exact=True).press("Enter")
    expect(page.get_by_role("button",name="Intercom outage",exact=True)).to_be_visible()
    page.get_by_role("button",name="Back to Analytics").click()
    expect(page.get_by_label("Category",exact=True)).to_have_value("OTHER")
    page.get_by_label("From date").fill("2000-01-01")
    page.get_by_label("Through date").fill("2000-01-01")
    page.get_by_role("button",name="Apply filters",exact=True).click()
    expect(page.get_by_text("No incidents match this selection.")).to_be_visible()
    for width in (320,390,1280):
        page.set_viewport_size({"width":width,"height":850})
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth"), (width, page.evaluate("""() => [...document.querySelectorAll('main *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('svg')).slice(0,15).map(e => [e.tagName,e.id,e.className,e.getBoundingClientRect().right])"""))
    assert not errors, errors
    print("PASS official logo, theme persistence, microphone fallback, report preview, campus pin, override, audit, drill-down and responsive dashboard")
    print("PASS real recording and local transcription" if voice_enabled else "SKIP real recording: RESOLV_VOSK_MODEL is unset")
    browser.close()
