"""Deterministic final checks before a proposed support decision is posted."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

from agent.schema import Decision, faq_sections


@dataclass
class GroundingResult:
    ok: bool
    problems: list[str]


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


_INTERNAL_TARGET = (
    r"(?:(?:het|de|een|ons|onze|mijn)\s+)?(?:(?:ander|andere|nieuw|nieuwe|tweede)\s+)?"
    r"(?:basis|plus|vestiging|locatie|abonnement|rooster|dienst|functie|telefoon|apparaat|taal|account)\b"
)

# Each entry is (Dutch, English, German). Text is lower-cased and accent-folded first.
# A pattern must describe a request or a threat, not just a topic word: "lek", "bezwaar",
# "juridisch" (a menu name in F09) or "overstappen naar Plus" (F05) are ordinary words.
# A false positive costs one human handoff; a false negative sends a forbidden reply.
_HANDOFF_PATTERNS = {
    "refund_or_dispute": (
        r"\bterugbetal\w*|\bgeld\s+terug\w*|\bterug\s*(?:krijg\w*|stort\w*|vorder\w*|boek\w*|gestort|geboekt)"
        r"|\bcredit(?:nota|factuur|eren)\b|\b(?:dubbel|teveel|te\s+veel)\s+(?:afgeschreven|betaald|gefactureerd|in\s+rekening)"
        r"|\bstorner\w*|\bonterecht\s+(?:afgeschreven|gefactureerd)\b|\bbetwiste?\s+(?:betaling|afschrijving|factuur)\b"
        r"|\bbezwaar\s+(?:\w+\s+){0,2}(?:tegen|op)\s+(?:\w+\s+){0,2}(?:factuur|afschrijving|betaling|kosten|incasso)\b"
        r"|\bniet\s+eens\s+met\s+(?:de|deze|onze)\s+(?:factuur|afschrijving)",
        r"\brefund\w*|\bchargeback\b|\bmoney\s+back\b|\breimburs\w*|\b(?:double|over)\s*charg\w*|\bcharged\s+twice\b"
        r"|\bdisput\w*\s+(?:\w+\s+){0,2}(?:charg\w*|payment\w*|bill\w*|invoice\w*)|\bwrongly\s+charg\w*",
        r"\bruckerstatt\w*|\bgeld\s+zuruck\b|\bunberechtigt\s+abgebucht\b|\blastschrift\s+widersprech\w*|\bzahlung\s+anfecht\w*",
    ),
    "cancellation_or_contract_change": (
        r"\bopzeg\w*|\bopgezegd\b|\bzeg\w*\s+(?:\w+\s+){0,3}(?:abonnement|contract|account)\s+(?:\w+\s+){0,3}op\b"
        r"|\b(?:abonnement|contract|account)\s+(?:\w+\s+){0,2}(?:stopzet\w*|stoppen|beeindig\w*|annuler\w*|ontbind\w*|pauzer\w*)"
        r"|\b(?:stopzet\w*|stoppen\s+met|beeindig\w*|annuler\w*|ontbind\w*)\s+(?:\w+\s+){0,3}(?:abonnement|contract|account|roosterly)"
        r"|\bopzegtermijn\b|\bcontractduur\b|\bcontract\s+(?:wijzig\w*|aanpass\w*)|\bdowngrad\w*|\blager\s+abonnement\b",
        r"\bcancel\w*\s+(?:\w+\s+){0,3}(?:account|subscription|plan|contract|membership|roosterly)\b"
        r"|\b(?:account|subscription|plan|contract|membership)\s+(?:\w+\s+){0,3}cancel\w*|\bclose\s+(?:my|our)\s+account\b|\bend\s+(?:my|our)\s+(?:subscription|contract)\b"
        r"|\bterminat\w*\s+(?:\w+\s+){0,2}(?:contract|subscription|plan)|\bcontract\s+chang\w*|\bdowngrad\w*",
        r"\bkundig\w*|\bvertrag\s+(?:ander\w*|beend\w*)|\babonnement\s+beend\w*|\btarif\s+herabstuf\w*",
    ),
    "personal_data_request": (
        r"\bavg\b|\binzageverzoek\b|\binzage\s+(?:in|van)\s+(?:\w+\s+){0,2}(?:gegevens|persoonsgegevens|data)\b"
        r"|\brecht\s+(?:op|om)\s+(?:vergetelheid|vergeten|inzage)|\bprivacy\s*verzoek\b"
        r"|\b(?:verwijder\w*|wis\w*)\s+(?:\w+\s+){0,3}(?:gegevens|data|account)\b"
        r"|\b(?:gegevens|data|account)\s+(?:\w+\s+){0,3}(?:laten\s+)?(?:verwijder\w*|wissen|gewist|export\w*|opvrag\w*)"
        r"|\bkopie\s+van\s+(?:\w+\s+){0,2}(?:gegevens|data)\b",
        r"\bgdpr\b|\bright\s+to\s+(?:be\s+forgotten|erasure|access)|\bdata\s+subject\b"
        r"|\b(?:delete|remove|erase|export)\s+(?:\w+\s+){0,3}(?:data|account)\b|\bcopy\s+of\s+(?:\w+\s+){0,2}data\b"
        r"|\bpersonal\s+data\s+(?:request|export|delet\w*|remov\w*|access)|\bdata\s+(?:deletion|erasure)\s+request\b",
        r"\bdsgvo\b|\b(?:meine|unsere)\s+daten\s+(?:losch\w*|export\w*|herausgeb\w*)|\bdatenauskunft\b|\bdatenlosch\w*",
    ),
    "security_incident": (
        r"\bgehack\w*|\bphishing\b|\bdatalek\w*|\b(?:data|gegevens)\s*lek\b|\bgelekt\b|\bbeveiligingsincident\w*|\binbraak\b"
        r"|\b(?:onbekend\w*|vreemd\w*|verdacht\w*)\s+(?:\w+\s+){0,2}(?:login\w*|inlog\w*|aanmeld\w*|toegang|activiteit\w*|apparaat|e-?mail\w*)"
        r"|\bingelogd\s+(?:\w+\s+){0,4}(?:onbekend\w*|vreemd\w*)|\biemand\s+anders\s+(?:\w+\s+){0,3}(?:ingelogd|toegang)"
        r"|\bniet\s+zelf\s+(?:ingelogd|gedaan|gewijzigd)|\bonbevoegd\w*\s+toegang\b|\baccount\s+overgenomen\b",
        r"\bhack\w*|\bdata\s+(?:breach|leak)\w*|\bsecurity\s+(?:incident|breach)\b|\bunauthori[sz]ed\s+access\b"
        r"|\b(?:unknown|suspicious|strange)\s+(?:\w+\s+){0,2}(?:login\w*|sign.?in\w*|access|activity|device|e-?mail\w*)"
        r"|\bsomeone\s+else\s+(?:\w+\s+){0,3}(?:logged|access)|\baccount\s+(?:\w+\s+){0,2}compromis\w*",
        r"\bunbekannte\s+anmeldung\b|\bdatenleck\w*|\bunbefugt\w*\s+zugriff\b|\bkonto\s+kompromittiert\b",
    ),
    "legal_threat": (
        r"\badvoca\w*|\bjurist\w*|\bjuridische?\s+(?:stappen|actie\w*|procedure\w*|afdeling)|\brechtszaak\b|\brechter\b|\brechtbank\b"
        r"|\baansprakelijk\s+(?:\w+\s+){0,2}stel\w*|\bstel\w*\s+(?:\w+\s+){0,4}aansprakelijk\b|\bin\s*gebreke\s*stel\w*"
        r"|\bschadevergoeding\b|\bschadeclaim\b|\baanklag\w*|\bconsumentenbond\b|\bautoriteit\s+persoonsgegevens\b|\bincassobureau\b",
        r"\blawyers?\b|\bsolicitor\b|\battorney\b|\blegal\s+(?:action|steps|counsel|proceedings)\b|\bsue\b|\blawsuit\b"
        r"|\b(?:to|in)\s+court\b|\bcourt\s+(?:action|case|proceedings)\b|\bhold\s+(?:you|roosterly)\s+liable\b|\bclaim\w*\s+damages\b",
        r"\banwalt\w*|\brechtlich\w*\s+schritt\w*|\bklage\s+einreich\w*|\bverklag\w*",
    ),
    "churn_threat": (
        # Switching plan (F05), location (F07) or device is not leaving Roosterly.
        r"\b(?:overstap\w*|over\s+te\s+stappen)\b(?!\s+(?:van|naar|op)\s+" + _INTERNAL_TARGET + r")"
        r"|\bstap\w*\s+(?:\w+\s+){0,3}over\s+naar\s+(?!" + _INTERNAL_TARGET + r")"
        r"|\bnaar\s+(?:een|de)\s+concurrent\b"
        r"|\b(?:overstap\w*|stap\w*|ga|gaan|vertrek\w*|kijk\w*|zoek\w*|wissel\w*|kiez\w*)\s+(?:\w+\s+){0,4}(?:naar|voor|bij)\s+(?:een\s+)?andere\s+(?:aanbieder|leverancier)\b"
        r"|\bweg\s+bij\s+(?:jullie|roosterly)\b|\bklaar\s+mee\b|\bzoeken\s+(?:\w+\s+){0,2}alternatief|\bik\s+vertrek\b|\bwe\s+gaan\s+weg\b",
        r"\b(?:switch\w*|leav\w*|mov\w*|go\w*)\s+(?:\w+\s+){0,2}(?:to|for)\s+(?:a\s+|another\s+|your\s+)?competitor\w*"
        r"|\b(?:switch\w*|mov\w*|leav\w*|go\w*|look\w*)\s+(?:\w+\s+){0,4}(?:to|for|at)\s+(?:another|a\s+different)\s+(?:provider|vendor)\b|\blook\w*\s+(?:for|at)\s+alternatives\b|\btak\w*\s+our\s+business\s+elsewhere\b",
        r"\b(?:zu|zum|zur)\s+(?:einem\s+|einer\s+)?(?:konkurren\w*|mitbewerber\w*)|\banbieter\s+wechsel\w*|\bander\w*\s+anbieter\b",
    ),
}


def detect_mandatory_handoff(subject: str, body: str) -> list[str]:
    """Catch client-forbidden automation in Dutch, English and German."""
    text = _fold(f"{subject} {body}")
    return [name for name, patterns in _HANDOFF_PATTERNS.items() if any(re.search(p, text) for p in patterns)]


def is_spam_or_empty(subject: str, body: str) -> bool:
    """Allow closing only content-free tickets and unmistakable promotions.

    A wrong close means a customer is ignored, so every spam pattern needs sales wording
    ("gratis analyse", "boost your rankings"), not just a topic word such as SEO or crypto.
    """
    # "Re:" / "Fwd:" with nothing after it is an empty ticket.
    subject = re.sub(r"^(?:\s*(?:re|fw|fwd|aw|antw|doorst)\s*:\s*)+", "", subject, flags=re.I)
    text = f"{subject} {body}".strip()
    if not re.search(r"\w", text, re.UNICODE) or re.fullmatch(r"test[.!?\s]*", text, re.I):
        return True
    if re.fullmatch(r"(?:(?:https?://|www\.)\S+\s*)+", text, re.I):
        return True
    folded = _fold(text)
    return bool(re.search(
        r"\b(?:seo|backlinks?|linkbuilding|guest posts?)\b.{0,120}\b(?:services?|agency|bureau|agentur)\b.{0,120}"
        r"(?:\bboost\s+(?:your\s+)?(?:rankings?|traffic)\b|\bbrengen\s+uw\s+website\b|\bgratis\s+analyse\b|\bfree\s+audit\b|\breageer\b)"
        r"|\b(?:bovenaan|hoger|top)\s+in\s+google\b.{0,160}(?:\bgratis\s+analyse\b|\bfree\s+audit\b|\breageer\b|\bmeld\s+u\s+aan\b)"
        r"|\b(?:bitcoin|crypto|nft|casino|gambling|slots?)\b.{0,60}\b(?:bonus|promotion|profits?|investment)\b"
        r"|\b(?:verdubbel\w*|verdoppel\w*|double)\b.{0,40}\b(?:geld|money|inkomen|income)\b"
        r".{0,80}\b(?:crypto\w*|bitcoin\w*|casino\w*|dagen|days|tagen)\b",
        folded, re.S,
    ))


_IBAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?:[ -]?[A-Za-z0-9]{2,4}){2,8}(?![A-Za-z0-9])")
_IBAN_LENGTHS = {"NL": 18, "DE": 22, "BE": 16, "GB": 22, "FR": 27, "ES": 24, "IT": 27, "AT": 20, "CH": 21, "LU": 20, "IE": 22, "PT": 25}
_CARD_RE = re.compile(r"(?<![\d+])\d(?:[ -]?\d){12,18}(?!\d)")


def _mod97(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    value = 0
    for c in rearranged:
        digits = c if c.isdigit() else str(ord(c) - ord("A") + 10)
        for digit in digits:
            value = (value * 10 + int(digit)) % 97
    return value == 1


def _iban_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in _IBAN_RE.finditer(text):
        raw = match.group()
        compact = re.sub(r"[ -]", "", raw).upper()
        country = compact[:2]
        length = _IBAN_LENGTHS.get(country)
        if length and len(compact) >= length:
            compact = compact[:length]
        elif not 15 <= len(compact) <= 34:
            continue
        shape = (country == "NL" and bool(re.fullmatch(r"NL\d{2}[A-Z]{4}\d{10}", compact))
                 or country == "DE" and bool(re.fullmatch(r"DE\d{20}", compact))
                 or country == "BE" and bool(re.fullmatch(r"BE\d{14}", compact)))
        if not (shape or _mod97(compact)):
            continue
        count = 0
        end = match.start()
        for i, c in enumerate(raw):
            if c not in " -":
                count += 1
            if count == len(compact):
                end = match.start() + i + 1
                break
        spans.append((match.start(), end))
    return spans


def _luhn(number: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(number)):
        digit = int(c)
        if i % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _card_spans(text: str) -> list[tuple[int, int]]:
    """Luhn-valid 13-19 digit runs, plus any 4-4-4-4 group (a mistyped card is still a card)."""
    spans = []
    for match in _CARD_RE.finditer(text):
        raw = match.group()
        digits = re.sub(r"\D", "", raw)
        grouped = bool(re.fullmatch(r"\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}", raw))
        if grouped or _luhn(digits):
            spans.append(match.span())
    return spans


def scrub_payment_data(text: str) -> str:
    """Redact even mistyped NL/DE/BE IBANs and grouped cards."""
    iban_spans = _iban_spans(text)
    # Avoid treating the numeric portion of an IBAN as a card.
    cards = [span for span in _card_spans(text) if not any(a < span[1] and span[0] < b for a, b in iban_spans)]
    for start, end, replacement in sorted(
        [(a, b, "[IBAN removed]") for a, b in iban_spans]
        + [(a, b, "[card number removed]") for a, b in cards], reverse=True
    ):
        text = text[:start] + replacement + text[end:]
    return text


def contains_payment_data(text: str) -> bool:
    """Use the same evidence as the scrubber so detection and removal agree."""
    return scrub_payment_data(text) != text


def _allowed_faq(faq_ids: list[str] | None) -> tuple[str, list[str]]:
    sections = faq_sections()
    unknown = [faq_id for faq_id in (faq_ids or []) if faq_id not in sections]
    ids = faq_ids if faq_ids else list(sections)
    return "\n".join(sections[i] for i in ids if i in sections), [f"unknown FAQ id: {i}" for i in unknown]


_URL_RE = re.compile(r"\b(?:https?://)?(?:[\w-]+\.)+[A-Za-z]{2,}(?:/[\w./?=&%-]+)?", re.I)
_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)")
_PATH_RE = re.compile(r"\b(?:[A-ZÀ-ÖØ-Ý][\w.-]*(?:\s+[a-z][\w.-]*){0,2}\s*→\s*)+[A-ZÀ-ÖØ-Ý][\w.-]*(?:\s+[a-z][\w.-]*){0,2}")
_QUOTE_RE = re.compile(r"[\"“”]([^\"“”\n]+)[\"“”]|(?<!\w)['‘]([^'‘’\n]+)['’](?!\w)")


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", _fold(text)).strip()


def _to_float(number: str) -> float:
    return float(number.replace(",", "."))


_MONEY = r"(?:€|eur|euro)"
_AMOUNT = rf"{_MONEY}\s*(\d+(?:[.,]\d+)?)(?:,-)?|(\d+(?:[.,]\d+)?)(?:,-|\s*{_MONEY})"
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])(?<!excl\.)(?<!incl\.)(?<!e\.g\.)(?<!i\.e\.)\s+|\n+")


def _amounts(text: str) -> set[float]:
    return {_to_float(a or b) for a, b in re.findall(_AMOUNT, text, re.I)}


def _plan_prices(faq_text: str) -> dict[str, float]:
    """Read each plan's per-employee price from the cited FAQ, never from code constants."""
    return {name.lower(): _to_float(price) for name, price in re.findall(
        rf"\b([A-Z]\w+):\s*{_MONEY}\s*(\d+(?:[.,]\d+)?)", faq_text
    )}


def _plan_mentions(text: str, plan_re: re.Pattern[str]) -> list[str]:
    """Find named plans, including compounds, but ignore 'op basis van' and 'plus VAT'."""
    text = re.sub(r"\bop\s+basis\s+van\b|\bplus\s+(?:btw|vat|mwst)\b", " ", text, flags=re.I)
    return list(dict.fromkeys(match.group().lower() for match in plan_re.finditer(text)))


def _plan_price_problems(reply: str, faq_text: str) -> list[str]:
    """Pair plans and known unit prices in reading order; reject ambiguous comparisons."""
    prices = _plan_prices(faq_text)
    if not prices:
        return []
    plan_re = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, prices)) + r")", re.I)
    problems: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(reply):
        plans = _plan_mentions(sentence, plan_re)
        units = [amount for amount in _amounts_in_order(sentence)
                 if any(abs(amount - price) < 0.005 for price in prices.values())]
        if not plans or not units:
            continue
        if len(plans) == 1:
            wrong = any(abs(amount - prices[plans[0]]) >= 0.005 for amount in units)
        else:
            wrong = len(plans) != len(units) or any(
                abs(amount - prices[plan]) >= 0.005 for plan, amount in zip(plans, units)
            )
        if wrong:
            problems.append(f"wrong price for {', '.join(plans)}: {sentence.strip()}")
    return problems


def _amounts_in_order(text: str) -> list[float]:
    return [_to_float(a or b) for a, b in re.findall(_AMOUNT, text, re.I)]


def _allowed_numbers(faq_text: str, ticket_text: str, reply: str = "") -> set[float]:
    """Numbers a reply may contain.

    - numbers from the cited FAQ sections;
    - numbers the customer wrote ("iOS 15", "25 medewerkers"): repeating them is not a new fact;
    - a price total (25 employees x EUR 6 = EUR 150), only when the sentence names
      one plan, or names none and the whole reply names just one. Multi-plan totals
      are ambiguous and go to a human. The count and money total must share a sentence.
    """
    faq = {_to_float(n) for n in _NUMBER_RE.findall(faq_text)}
    own = {_to_float(n) for n in _NUMBER_RE.findall(ticket_text)}
    allowed = faq | own
    plan_prices = _plan_prices(faq_text)
    if not plan_prices:
        return allowed
    plan_re = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, plan_prices)) + r")", re.I)
    reply_plans = _plan_mentions(reply, plan_re)
    for sentence in _SENTENCE_SPLIT_RE.split(reply):
        sentence_plans = _plan_mentions(sentence, plan_re)
        plans = sentence_plans or reply_plans
        if len(plans) != 1:
            continue
        counts = own & {_to_float(n) for n in _NUMBER_RE.findall(sentence)}
        for total in _amounts(sentence):
            if any(abs(count * plan_prices[plans[0]] - total) < 0.005 for count in counts):
                allowed.add(total)
    return allowed


def check_grounding(reply: str, faq_ids: list[str] | None = None, *, ticket_text: str = "") -> GroundingResult:
    """Block checkable facts and UI instructions absent from the cited FAQ.

    `ticket_text` is the customer's own message: repeating a number from it is allowed.
    """
    allowed, problems = _allowed_faq(faq_ids)
    if not reply.strip():
        problems.append("empty reply")
    allowed_norm = _normal(allowed)
    allowed_numbers = _allowed_numbers(allowed, ticket_text, reply)
    problems.extend(_plan_price_problems(reply, allowed))
    for number in sorted(set(_NUMBER_RE.findall(reply))):
        if not any(abs(_to_float(number) - ok) < 0.005 for ok in allowed_numbers):
            problems.append(f"unsupported number: {number}")
    for url in set(_URL_RE.findall(reply)):
        clean_url = re.sub(r"^https?://", "", url.lower()).rstrip(".,;:!?")
        if clean_url not in allowed.lower():
            problems.append(f"unsupported URL or domain: {url}")
    for path in set(_PATH_RE.findall(reply)):
        # The path regex may include the first words of the following clause.
        candidate = path.rstrip(".,;:!?")
        for _ in range(3):
            if _normal(candidate) in allowed_norm:
                break
            last = candidate.rsplit(" ", 1)[-1]
            if not last or not last[0].islower():
                problems.append(f"unsupported menu path: {path}")
                break
            candidate = candidate.rsplit(" ", 1)[0]
        else:
            problems.append(f"unsupported menu path: {path}")
    for label in {a or b for a, b in _QUOTE_RE.findall(reply)}:
        if _normal(label) not in allowed_norm:
            problems.append(f"unsupported quoted label: {label}")
    return GroundingResult(not problems, problems)


_STOP = set("a an the and or to in on at for from of with by as is are was were be been being it its this that these those you your we our i my me us they their he she his her do does did can could would should will may please per under into than then if not no only all every each who what where when how there here hello hi dear thanks thank kind regards sincerely best hope helps let know good morning afternoon evening go click tap enter fill open which via must also any".split())
# Whole sentences that carry no claim. Anything after a courtesy opener is still checked,
# so "Thanks, refunds are guaranteed" cannot hide behind the "Thanks".
_COURTESY_SENTENCE_RE = re.compile(
    r"(?:(?:hello|hi|hey|dear|good\s+(?:morning|afternoon|evening))(?:\s+[\w.'-]+){0,3}"
    r"|thanks?(?:\s+you)?(?:\s+(?:very\s+much|a\s+lot))?(?:\s+for\s+(?:your|the)\s+(?:message|question|e-?mail|patience|suggestion|idea|feedback|request|report)|\s+for\s+(?:reaching\s+out|contacting\s+us))?"
    r"|(?:i|we)?\s*hope\s+(?:this|that)\s+helps(?:\s+you)?"
    r"|(?:please\s+)?(?:let\s+us\s+know|feel\s+free\s+to\s+contact\s+us|do\s+not\s+hesitate\s+to\s+contact\s+us)\s+if\s+(?:you\s+have|there\s+are)\s+(?:any\s+)?(?:further\s+|more\s+|other\s+)?questions"
    r"|(?:with\s+)?(?:kind|best|warm|friendly)\s+(?:regards|greetings?)(?:[\s,]+[\w.'-]+){0,4}|sincerely(?:[\s,]+[\w.'-]+){0,4}|greetings(?:[\s,]+[\w.'-]+){0,3}"
    r"|(?:the\s+|team\s+)?roosterly(?:\s+(?:support|team|customer\s+service|helpdesk))*|(?:the\s+)?(?:support|customer\s+service)\s+team)[\s,.!]*",
    re.I,
)
_GREETING_PREFIX_RE = re.compile(r"^(?:hello|hi|hey|dear|good\s+(?:morning|afternoon|evening))(?:\s+[\w.'-]+){0,3}\s*,\s*", re.I)
# Words a translator may leave in Dutch or swap for an everyday equivalent of the FAQ's word.
_EQUIVALENTS = {"btw": "vat", "schedul": "plann", "worker": "employe"}


def _stem(word: str) -> str:
    """Crude stemmer so approve/approved/approves and employee/employees compare equal."""
    for suffix in ("ing", "ied", "ies", "ed", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[:-len(suffix)] + ("y" if suffix in ("ied", "ies") else "")
            break
    return word[:-1] if len(word) > 3 and word.endswith("e") else word


def _words(text: str) -> list[str]:
    """Content words of `text`, stemmed. "e-mail" (with any kind of hyphen) counts as "email"."""
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", _fold(text))  # typographic hyphens and dashes
    text = re.sub(r"\be-mail", "email", text)
    return [_stem(w) for w in re.findall(r"[a-zà-ÿ0-9]+", text) if w not in _STOP]


def _supported(word: str, known: set[str]) -> bool:
    """A word is supported if the FAQ has it, or if it is two FAQ words written as one.

    Dutch and German join words that English and the FAQ write apart: "spamfolder",
    "resetlink". A literal translation often keeps them joined.
    """
    if word in known:
        return True
    return any(_stem(word[:i]) in known and word[i:] in known for i in range(3, len(word) - 2))


# Promise, money and deadline words. A reply may paraphrase the FAQ, but it may never use
# one of these unless the cited FAQ section uses it too, whatever the overlap score is.
_RISK_WORDS = set(_stem(w) for w in """refund refunds reimburse reimbursement compensate compensation credit credited
    discount free bonus gift guarantee guaranteed guarantees promise promised cancel cancellation terminate
    today tomorrow tonight immediately soon shortly hours days weeks week within deadline
    fix fixed resolved resolve solved escalate escalated call callback contact investigate investigating
    sorry apologize apologise apologies lawyer legal liable liability delete deleted erase erased
    public publicly visible exposed leaked""".split())
_NEGATION_RE = re.compile(r"\b(?:not|no|never|cannot|unsupported|unable)\b|n't", re.I)


def _version_problems(sentence: str, faq_text: str) -> list[str]:
    """Word overlap cannot see negation, so check minimum versions exactly.

    The FAQ says "iOS 16 and later". Wrong: "works on iOS 15", "iOS 16 is not supported".
    Right: "iOS 15 is not supported", "requires iOS 16 or later". Negation is judged per
    clause, and "older than iOS 16" style comparisons are left alone.
    """
    out = []
    for system, minimum in re.findall(r"\b(iOS|Android)\s+(\d+)\s+and\s+later", faq_text):
        for clause in re.split(r"[,;:()]|\b(?:but|so|because|which)\b", sentence):
            clause = re.sub(rf"\b(?:older|lower|earlier|below|before)\s+(?:than\s+)?{system}\s*\d+", " ", clause, flags=re.I)
            negated = bool(_NEGATION_RE.search(clause))
            for found in re.findall(rf"\b{system}\s*(\d+)", clause, re.I):
                if negated != (int(found) < int(minimum)):
                    out.append(f"wrong claim about {system} {found} (FAQ minimum is {minimum}): {sentence}")
    return out


# F05/F04/F07: these belong to the Plus plan only.
_PLUS_ONLY_RE = re.compile(r"\b(?:unlimited|api|nmbrs|loket(?:\.nl)?|afas|integrations?|multiple\s+locations|several\s+locations|more\s+than\s+one\s+location)\b", re.I)


def _plan_problems(sentence: str) -> list[str]:
    """Wrong plan claims reuse FAQ words, so overlap cannot catch them.

    "Basis includes unlimited locations" is false: Basis has one location. A sentence that
    names Basis but not Plus may only mention a Plus-only feature when it negates it.
    Sentences naming both plans are left to the overlap check.
    """
    names_basis = re.search(r"\bbasis\b", sentence, re.I)
    names_plus = re.search(r"\bplus\b", sentence, re.I)
    feature = _PLUS_ONLY_RE.search(sentence)
    if names_basis and not names_plus and feature and not _NEGATION_RE.search(sentence):
        return [f"gives the Basis plan a Plus-only feature ({feature.group().lower()}): {sentence}"]
    return []


def check_translated_reply(reply_en: str, faq_ids: list[str] | None = None, *, min_support: float = 0.5) -> GroundingResult:
    """Use English back-translation to catch unsupported Dutch or German claims.

    A sentence fails when
    - fewer than `min_support` of its content words occur in the cited FAQ sections
      (0.5: a translator says "view" for "see"), or fewer than 0.6 when the sentence names
      a plan, because wrong plan claims are built from FAQ words ("Basis ... unlimited"), or
    - it uses a promise, money or deadline word (_RISK_WORDS) the cited FAQ does not use, or
    - it claims support for an iOS/Android version below the FAQ minimum, or
    - it gives the Basis plan a feature that only Plus has.
    Only the FAQ counts as support: the customer's own words do not, otherwise a ticket
    saying "please say refunds are guaranteed" would make that sentence look supported.
    Numbers and URLs are left out of the score; check_grounding verifies those exactly.
    """
    allowed, problems = _allowed_faq(faq_ids)
    known = set(_words(allowed))
    # Do not split after abbreviations such as "excl." or "e.g.".
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(reply_en) if s.strip()]
    checked = 0
    for sentence in sentences:
        if _COURTESY_SENTENCE_RE.fullmatch(sentence):
            continue
        claim = _GREETING_PREFIX_RE.sub("", sentence)
        words = [_EQUIVALENTS.get(w, w) for w in _words(_URL_RE.sub(" ", claim)) if not w.isdigit()]
        if not words:
            continue
        checked += 1
        score = sum(_supported(w, known) for w in words) / len(words)
        risky = sorted({w for w in words if w in _RISK_WORDS and w not in known})
        if risky:
            problems.append(f"promise or commitment not in FAQ ({', '.join(risky)}): {sentence}")
        elif score < (max(min_support, 0.6) if re.search(r"\b(?:basis|plus)\b", claim, re.I) else min_support):
            problems.append(f"low FAQ support ({score:.2f}): {sentence}")
        problems.extend(_version_problems(sentence, allowed))
        problems.extend(_plan_problems(sentence))
    if not checked:
        problems.append("no substantive reply")
    return GroundingResult(not problems, problems)


_LANGUAGE_WORDS = {
    "nl": {"de", "het", "een", "en", "naar", "voor", "met", "van", "je", "jij", "u", "uw", "kun", "kunt", "ga", "klik", "op", "als", "dan", "ik", "mijn", "hoe", "wat", "kan"},
    "en": {"the", "and", "to", "for", "with", "from", "you", "your", "can", "go", "click", "on", "if", "then", "please", "are", "of", "at", "this", "that", "be", "will", "which", "there", "see", "no", "how", "what"},
    "de": {"der", "die", "das", "ein", "eine", "und", "zu", "zum", "zur", "mit", "von", "sie", "ihre", "konnen", "gehen", "klicken", "auf", "wenn", "werden", "ist", "sind", "nicht", "ich", "mein", "wie", "kann"},
}
_GREETING_LINE_RE = re.compile(r"^\s*(?:hi|hello|hey|dear|hoi|beste|hallo|liebe[rs]?|sehr\s+geehrte[rs]?)\b[^,\n]*,", re.I)
# Three strong cues identify a ticket; two competing cues are enough to stop a reply.
_LANGUAGE_CONFIDENT_HITS = 3
_LANGUAGE_MISMATCH_HITS = 2


def _language_scores(text: str) -> dict[str, int]:
    """Count language cues after excluding greetings, URLs, quoted labels and menu paths."""
    text = _GREETING_LINE_RE.sub(" ", text)
    for pattern in (_QUOTE_RE, _URL_RE, _PATH_RE):
        text = pattern.sub(" ", text)
    words = re.findall(r"[a-z]+", _fold(text))
    return {language: sum(word in cues for word in words) for language, cues in _LANGUAGE_WORDS.items()}


def detect_reply_language(text: str) -> str | None:
    """Return a language only when at least three cues beat the runner-up twofold."""
    scores = _language_scores(text)
    ranked = sorted(scores, key=scores.get, reverse=True)
    return ranked[0] if scores[ranked[0]] >= _LANGUAGE_CONFIDENT_HITS and scores[ranked[0]] >= 2 * scores[ranked[1]] else None


def _reply_language_mismatch(text: str, expected: str) -> bool:
    """Catch a clear competing language, including mixed replies with a foreign clause."""
    if expected not in _LANGUAGE_WORDS:
        return False
    scores = _language_scores(text)
    other = max(score for language, score in scores.items() if language != expected)
    return (other >= _LANGUAGE_MISMATCH_HITS and other >= scores[expected]) or other >= _LANGUAGE_CONFIDENT_HITS


def _add_override(decision: Decision, name: str) -> None:
    if name not in decision.overrides:
        decision.overrides.append(name)


def _force_handoff(decision: Decision, rule: str, original_action: str, original_context: str) -> None:
    decision.action = "handoff"
    _add_override(decision, rule)
    context = re.sub(r"\s+", " ", original_context).strip() or "Review the customer's request in the ticket."
    faq_hint = f" The withheld draft was based on FAQ {', '.join(decision.faq_ids)}." if decision.faq_ids else ""
    decision.handoff_note = (
        f"Rule: {rule}. Customer request: {context} Original proposed action: {original_action}.{faq_hint} "
        "Suggested next step: read the ticket and answer it manually."
    )
    decision.draft_reply = ""
    decision.confidence = None  # the model's confidence was about its own proposal, not this handoff
    # The API wants `reason` to explain category, priority and action in one or two sentences.
    first = re.split(r"(?<=[.!?])\s+", decision.reason.strip(), maxsplit=1)[0].rstrip(".!? ")
    first = first or "Customer request needs review"
    decision.reason = f"{decision.category} ticket at {decision.priority}: {first}. Safety rule {rule} forced a handoff."


def apply_rules(ticket: dict, decision: Decision, *, translate: Callable[[str, str], str] | None = None) -> Decision:
    """Give client rules the final word over the model's proposed action."""
    original_action = decision.action
    original_context = decision.handoff_note or decision.reason
    subject, body = str(ticket.get("subject") or ""), str(ticket.get("body") or "")
    # Note an invalid proposal now: a later rule may overwrite the bad value (e.g. priority).
    proposal_invalid = bool(decision.problems())
    mandatory = detect_mandatory_handoff(subject, body)
    if mandatory:
        level = "P1" if any(r in mandatory for r in ("security_incident", "legal_threat")) else "P2"
        if decision.priority != "P1" and (level == "P1" or decision.priority != "P2"):
            decision.priority = level
            _add_override(decision, f"priority_raised:{level}")
        if decision.action != "handoff":
            _force_handoff(decision, f"mandatory_handoff:{mandatory[0]}", original_action, original_context)
            for rule in mandatory[1:]:
                _add_override(decision, f"mandatory_handoff:{rule}")
    if decision.action == "close" and not is_spam_or_empty(subject, body):
        _force_handoff(decision, "close_not_allowed", original_action, original_context)
    if decision.action == "reply":
        ticket_text = f"{subject} {body}"
        ticket_language = detect_reply_language(ticket_text)
        if ticket_language is not None and ticket_language != decision.language:
            _force_handoff(decision, "ticket_language_mismatch", original_action, original_context)
        elif not check_grounding(decision.draft_reply, decision.faq_ids, ticket_text=ticket_text).ok:
            _force_handoff(decision, "ungrounded_reply", original_action, original_context)
        elif _reply_language_mismatch(decision.draft_reply, decision.language):
            _force_handoff(decision, "reply_language_mismatch", original_action, original_context)
        elif decision.language != "en" and translate is None:
            # Nobody here reads Dutch or German: without a back-translation the reply is unchecked.
            _force_handoff(decision, "translation_check_missing", original_action, original_context)
        else:
            try:
                reply_en = decision.draft_reply if decision.language == "en" else translate(decision.draft_reply, decision.language)
                if not check_translated_reply(reply_en, decision.faq_ids).ok:
                    _force_handoff(decision, "translation_check_failed", original_action, original_context)
            except Exception:
                _force_handoff(decision, "translation_check_failed", original_action, original_context)
    if decision.problems() or proposal_invalid:
        decision.language = decision.language if decision.language in ("nl", "en", "de") else "nl"
        decision.category = decision.category if decision.category in ("bug", "billing", "feature_request", "question", "other") else "other"
        decision.priority = decision.priority if decision.priority in ("P1", "P2", "P3", "P4") else "P3"
        decision.confidence = decision.confidence if isinstance(decision.confidence, (int, float)) and 0 <= decision.confidence <= 1 else None
        _force_handoff(decision, "invalid_decision", original_action, original_context)
    for field in ("draft_reply", "handoff_note", "reason"):
        value = getattr(decision, field)
        clean = scrub_payment_data(value)
        if clean != value:
            setattr(decision, field, clean)
            _add_override(decision, "payment_data_scrubbed")
    return decision


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d ()-]{7,}\d(?!\w)")


def sanitize_for_log(text: str, max_len: int = 200) -> str:
    """Make a short string safe to write to logs/: no payment details, email addresses or
    phone numbers, one line, limited length. The run loop uses it for rule names; free text
    such as the model's `reason` is not logged at all (see agent/run.py)."""
    text = scrub_payment_data(text)
    text = _EMAIL_RE.sub("[email removed]", text)
    text = _PHONE_RE.sub("[number removed]", text)
    return re.sub(r"\s+", " ", text).strip()[:max_len]
