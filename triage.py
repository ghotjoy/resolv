"""Read-only incident triage with a local fallback and opt-in provider adapter."""
from abc import ABC, abstractmethod
import json
from urllib.error import URLError
from urllib.request import Request, urlopen

from resolv import ValidationError


class TriageProvider(ABC):
    @abstractmethod
    def suggest(self, messages, categories): ...


class GeminiTriage(TriageProvider):
    def __init__(self, api_key, model="gemini-2.5-flash", transport=urlopen):
        self.api_key, self.model, self.transport = api_key, model, transport

    def suggest(self, messages, categories):
        settings = {
            "systemInstruction":{"parts":[{"text":"Suggest helpdesk triage only. Return JSON with category, impact, urgency and a short troubleshooting suggestion. Never request credentials. The valid categories are " + ", ".join(categories)}]},
            "contents":[{"role":"model" if m["role"] == "assistant" else "user","parts":[{"text":m["text"]}]} for m in messages],
            "generationConfig":{"responseMimeType":"application/json","maxOutputTokens":600},
        }
        request = Request(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                          json.dumps(settings).encode(),
                          {"Content-Type":"application/json","x-goog-api-key":self.api_key})
        with self.transport(request,timeout=5) as response:
            body = json.loads(response.read(50_000))
        text = "".join(part.get("text","") for part in body["candidates"][0]["content"]["parts"])
        return json.loads(text)


class Triage:
    def __init__(self, provider=None):
        self.provider = provider

    @staticmethod
    def validate(messages):
        if not isinstance(messages,list) or not 1 <= len(messages) <= 12:
            raise ValidationError("provide 1–12 triage messages")
        clean = []
        for m in messages:
            if not isinstance(m,dict) or m.get("role") not in ("user","assistant"):
                raise ValidationError("invalid triage message role")
            text = m.get("text")
            if not isinstance(text,str) or not 1 <= len(text.strip()) <= 2000:
                raise ValidationError("triage messages must contain 1–2,000 characters")
            clean.append(dict(role=m["role"],text=text.strip()))
        if clean[-1]["role"] != "user" or sum(len(m["text"]) for m in clean) > 12_000:
            raise ValidationError("the final message must be from the reporter and the total limit is 12,000 characters")
        return clean

    @staticmethod
    def local(category, latest):
        text = latest.casefold()
        broad = any(word in text for word in ("building-wide","everyone","whole campus","service down"))
        urgent = any(word in text for word in ("outage","unavailable","security","locked out"))
        return dict(category=category,impact=3 if broad else 1,urgency=3 if urgent else 1,
                    suggestion="Confirm who is affected, when the issue started, and any error message. Remove credentials from the report.",
                    source="rules")

    def suggest(self, messages, category, categories, use_provider=False):
        fallback = self.local(category,messages[-1]["text"])
        if self.provider is None or use_provider is not True:
            return fallback
        try:
            result = self.provider.suggest(messages,categories)
            advice = result["suggestion"]
            if (result["category"] not in categories or any(type(result[k]) is not int or not 1 <= result[k] <= 4
                                                            for k in ("impact","urgency")) or
                    not isinstance(advice,str) or not 1 <= len(advice.strip()) <= 600):
                return fallback
            return dict(category=result["category"],impact=result["impact"],urgency=result["urgency"],
                        suggestion=advice.strip(),source="provider")
        except (URLError,OSError,ValueError,KeyError,TypeError,IndexError,AttributeError):
            return fallback
