"""Métriques relationnelles déterministes à partir d'iMessage (sans LLM)."""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_FR_WEEKDAY = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _parse_message_dt(m: dict) -> datetime | None:
    d = m.get("date")
    if isinstance(d, datetime):
        return d.replace(tzinfo=None) if d.tzinfo else d
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _normalize_messages(raw: list[dict]) -> list[dict]:
    out = []
    for m in raw:
        dt = _parse_message_dt(m)
        if dt is None:
            continue
        out.append({**m, "date": dt})
    return out


class ContactAnalytics:
    """Calcul pur Python sur les messages bruts (pas de LLM)."""

    def __init__(self, imessage_reader: Any):
        self.reader = imessage_reader

    def compute_all(self, handle: str, name: str, days: int = 730) -> dict:
        """Calcule toutes les métriques pour un contact.

        Essaie progressivement des fenêtres plus longues si la fenêtre demandée
        ne retourne aucun message (contacts inactifs depuis > 90 jours).
        """
        if not self.reader or not getattr(self.reader, "is_available", lambda: False)():
            return {"error": "iMessage indisponible", "proximity_score": {"score": 0}}

        messages: list[dict] = []
        for period in sorted({days, 365, 730, 1825}):
            if period < days:
                continue
            raw = self.reader.get_conversation_for_period(handle, days=period, limit=5000)
            messages = _normalize_messages(raw)
            if messages:
                break

        if not messages:
            return {"error": "Aucun message trouvé", "proximity_score": {"score": 0}}

        return {
            "proximity_score": self._proximity_score(messages),
            "trend": self._trend(messages),
            "sentiment_heatmap": self._sentiment_heatmap(messages),
            "topics": self._topics(messages),
            "unanswered": self._unanswered(messages, name),
            "last_exchanges": self._last_exchanges(messages, name),
            "important_dates": self._important_dates(messages),
            "communication_patterns": self._communication_patterns(messages, name),
            "stats": self._detailed_stats(messages),
        }

    def _proximity_score(self, messages: list[dict]) -> dict:
        now = datetime.now()
        from_me = [m for m in messages if m["is_from_me"]]
        from_them = [m for m in messages if not m["is_from_me"]]
        total = len(messages)
        if total == 0:
            return {"score": 0, "breakdown": {}}

        recent = [m for m in messages if (now - m["date"]).days <= 30]
        freq_per_week = len(recent) / 4.3
        freq_score = min(20.0, freq_per_week * 2.0)

        last_msg_date = max(m["date"] for m in messages)
        days_since = (now - last_msg_date).days
        if days_since <= 0:
            recency_score = 20.0
        elif days_since <= 1:
            recency_score = 18.0
        elif days_since <= 3:
            recency_score = 15.0
        elif days_since <= 7:
            recency_score = 10.0
        elif days_since <= 14:
            recency_score = 5.0
        elif days_since <= 30:
            recency_score = 2.0
        else:
            recency_score = 0.0

        ratio = len(from_me) / max(len(from_them), 1)
        balance = 1.0 - abs(1.0 - ratio)
        balance_score = max(0.0, balance * 15.0)

        initiations_me = 0
        initiations_them = 0
        prev_time = None
        for m in sorted(messages, key=lambda x: x["date"]):
            if prev_time is not None and (m["date"] - prev_time).total_seconds() > 14400:
                if m["is_from_me"]:
                    initiations_me += 1
                else:
                    initiations_them += 1
            prev_time = m["date"]

        total_init = initiations_me + initiations_them
        if total_init > 0:
            init_balance = 1.0 - abs(0.5 - initiations_me / total_init) * 2.0
            initiative_score = max(0.0, init_balance * 15.0)
        else:
            initiative_score = 7.0

        lengths = [len((m.get("text") or "") or "") for m in messages]
        avg_len = statistics.mean(lengths) if lengths else 0.0
        length_score = min(15.0, avg_len / 10.0)

        emoji_pattern = re.compile(
            r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
            r"\U0001F900-\U0001F9FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F"
            r"\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF]"
        )
        msgs_with_emoji = sum(
            1 for m in messages if emoji_pattern.search((m.get("text") or "") or "")
        )
        emoji_ratio = msgs_with_emoji / max(total, 1)
        emoji_score = min(15.0, emoji_ratio * 30.0)

        total_score = int(
            freq_score + recency_score + balance_score + initiative_score + length_score + emoji_score
        )
        total_score = min(100, max(0, total_score))

        return {
            "score": total_score,
            "breakdown": {
                "frequency": round(freq_score, 1),
                "recency": round(recency_score, 1),
                "balance": round(balance_score, 1),
                "initiative": round(initiative_score, 1),
                "depth": round(length_score, 1),
                "affection": round(emoji_score, 1),
            },
            "details": {
                "msgs_per_week": round(freq_per_week, 1),
                "days_since_last": days_since,
                "ratio_sent_received": round(ratio, 2),
                "who_initiates_more": "you" if initiations_me > initiations_them else "them",
                "avg_message_length": round(avg_len),
                "emoji_usage_pct": round(emoji_ratio * 100, 1),
            },
        }

    def _trend(self, messages: list[dict]) -> dict:
        now = datetime.now()
        months: list[dict] = []
        for i in range(3):
            start = now - timedelta(days=30 * (i + 1))
            end = now - timedelta(days=30 * i)
            count = sum(1 for m in messages if start <= m["date"] < end)
            label = (now - timedelta(days=30 * i)).strftime("%b")
            months.append({"month": label, "count": count})
        months.reverse()
        # Ordre : [ancien, milieu, récent]
        old_c, mid_c, recent_c = months[0]["count"], months[1]["count"], months[2]["count"]
        if mid_c == 0 and old_c == 0:
            trend_pct = 0
            direction = "stable"
        elif mid_c == 0:
            trend_pct = 100 if recent_c > 0 else 0
            direction = "up" if recent_c > 0 else "stable"
        else:
            trend_pct = round(((recent_c - mid_c) / mid_c) * 100)
            if trend_pct > 10:
                direction = "up"
            elif trend_pct < -10:
                direction = "down"
            else:
                direction = "stable"

        label_fr = f"{'+' if trend_pct > 0 else ''}{trend_pct}% vs mois précédent"
        direction_fr = {"up": "Se rapproche", "down": "Se distend", "stable": "Stable"}[direction]

        return {
            "months": months,
            "trend_pct": trend_pct,
            "direction": direction,
            "direction_label": direction_fr,
            "label": label_fr,
        }

    def _sentiment_heatmap(self, messages: list[dict]) -> list[dict]:
        positive_signals = re.compile(
            r"[❤️💕💖😍🥰😘💪👍🎉😊😂🤣💜💙💚♥️]|merci|super|génial|adorable|parfait|bisous|love|cool|"
            r"haha|mdr|ptdr|trop bien|je t'aime|manque",
            re.IGNORECASE,
        )
        negative_signals = re.compile(
            r"[😡😤😢😭💔😠👎]|merde|chiant|relou|énervé|triste|désolé|pardon|conflit|problème|"
            r"dispute|nul|grave|colère|stress",
            re.IGNORECASE,
        )

        weeks: dict[str, dict[str, int]] = defaultdict(
            lambda: {"positive": 0, "negative": 0, "neutral": 0, "total": 0}
        )
        for m in messages:
            text = (m.get("text") or "") or ""
            week_key = m["date"].strftime("%Y-W%U")
            weeks[week_key]["total"] += 1
            pos = len(positive_signals.findall(text))
            neg = len(negative_signals.findall(text))
            if pos > neg:
                weeks[week_key]["positive"] += 1
            elif neg > pos:
                weeks[week_key]["negative"] += 1
            else:
                weeks[week_key]["neutral"] += 1

        result = []
        for week, counts in sorted(weeks.items()):
            total = counts["total"]
            if total == 0:
                score = 0.5
            else:
                score = (counts["positive"] - counts["negative"]) / total
            score = max(0.0, min(1.0, (score + 1) / 2))
            result.append(
                {
                    "week": week,
                    "sentiment_score": round(score, 2),
                    "total_messages": total,
                    "positive": counts["positive"],
                    "negative": counts["negative"],
                }
            )

        return result[-12:]

    def _topics(self, messages: list[dict]) -> list[dict]:
        stop_words = {
            "le",
            "la",
            "les",
            "de",
            "du",
            "des",
            "un",
            "une",
            "en",
            "et",
            "est",
            "je",
            "tu",
            "il",
            "on",
            "ce",
            "ça",
            "pas",
            "que",
            "qui",
            "ne",
            "se",
            "me",
            "te",
            "ai",
            "a",
            "mon",
            "ton",
            "son",
            "ma",
            "ta",
            "sa",
            "nous",
            "vous",
            "ils",
            "dans",
            "pour",
            "avec",
            "sur",
            "par",
            "plus",
            "mais",
            "ou",
            "où",
            "quoi",
            "fait",
            "faire",
            "dit",
            "dire",
            "bien",
            "tout",
            "très",
            "oui",
            "non",
            "moi",
            "toi",
            "lui",
            "elle",
            "rien",
            "aussi",
            "comme",
            "être",
            "avoir",
            "c'est",
            "j'ai",
            "t'as",
            "y'a",
            "ya",
            "nan",
            "ouais",
            "bah",
            "ben",
            "alors",
            "donc",
            "aimé",
            "adoré",
            "ajouté",
            "https",
            "http",
            "quand",
            "même",
            "après",
            "avant",
            "cette",
            "autre",
            "tous",
            "leur",
            "leurs",
            "vous",
            "chez",
            "sans",
            "sous",
            "entre",
            "etre",
            "cette",
            "cela",
            "ceux",
            "ceci",
            "voilà",
            "voila",
            "okay",
            "merci",
            "déjà",
            "deja",
            "juste",
            "assez",
            "encore",
            "toujours",
            "jamais",
            "peut",
            "avoir",
        }

        # Réactions iMessage à ignorer (tapbacks)
        _REACTION_PREFIXES = (
            "a aimé", "a adoré", "a ajouté", "a répondu avec",
            "liked", "loved", "emphasized", "questioned",
            "a mis un cœur", "a mis en valeur", "a interrogé",
        )

        word_count: Counter[str] = Counter()
        for m in messages:
            raw = (m.get("text") or "") or ""
            # Skip les réactions tapback
            low_raw = raw.lower().strip()
            if any(low_raw.startswith(pref) for pref in _REACTION_PREFIXES):
                continue
            # Skip les URLs
            if low_raw.startswith("http"):
                continue
            text = low_raw
            words = re.findall(r"\b[a-zàâäéèêëïîôùûüÿç]{4,}\b", text)
            for w in words:
                if w not in stop_words:
                    word_count[w] += 1

        return [{"word": word, "count": count} for word, count in word_count.most_common(15)]

    def _unanswered(self, messages: list[dict], name: str) -> dict:
        del name  # réservé pour futures heuristiques
        if not messages:
            return {"from_me": [], "from_them": []}

        sorted_msgs = sorted(messages, key=lambda x: x["date"])
        last = sorted_msgs[-1]
        now = datetime.now()
        hours_ago = (now - last["date"]).total_seconds() / 3600
        unanswered_by_me: list[dict] = []
        unanswered_by_them: list[dict] = []

        if hours_ago > 6:
            entry = {
                "text": ((last.get("text") or "") or "")[:100],
                "date": last["date"].isoformat(),
                "hours_ago": round(hours_ago),
            }
            if last["is_from_me"]:
                unanswered_by_them.append(entry)
            else:
                unanswered_by_me.append(entry)

        return {
            "from_me": unanswered_by_me[:5],
            "from_them": unanswered_by_them[:5],
        }

    def _last_exchanges(self, messages: list[dict], name: str) -> list[dict]:
        sorted_msgs = sorted(messages, key=lambda x: x["date"], reverse=True)
        chunk = sorted_msgs[:5]
        chunk.reverse()
        return [
            {
                "sender": "Moi" if m["is_from_me"] else name,
                "text": ((m.get("text") or "") or "")[:200],
                "date": m["date"].isoformat(),
                "is_from_me": m["is_from_me"],
            }
            for m in chunk
        ]

    def _important_dates(self, messages: list[dict]) -> list[dict]:
        date_patterns = re.compile(
            r"(anniversaire|anniv|birthday|fête|noël|noel|nouvel an|vacances|mariage|naissance)",
            re.IGNORECASE,
        )
        dates = []
        for m in messages:
            text = (m.get("text") or "") or ""
            match = date_patterns.search(text)
            if match:
                dates.append(
                    {
                        "keyword": match.group().lower(),
                        "context": text[:150],
                        "date": m["date"].isoformat(),
                        "is_from_me": m["is_from_me"],
                    }
                )
        return dates[-10:]

    def _communication_patterns(self, messages: list[dict], name: str) -> dict:
        del name
        from_me = [m for m in messages if m["is_from_me"]]
        from_them = [m for m in messages if not m["is_from_me"]]

        hours_me = Counter(m["date"].hour for m in from_me)
        hours_them = Counter(m["date"].hour for m in from_them)

        days_me = Counter(_FR_WEEKDAY[m["date"].weekday()] for m in from_me)
        days_them = Counter(_FR_WEEKDAY[m["date"].weekday()] for m in from_them)

        response_times_me: list[float] = []
        response_times_them: list[float] = []
        sorted_msgs = sorted(messages, key=lambda x: x["date"])
        for i in range(1, len(sorted_msgs)):
            prev = sorted_msgs[i - 1]
            curr = sorted_msgs[i]
            delta = (curr["date"] - prev["date"]).total_seconds()
            if delta < 86400:
                if prev["is_from_me"] and not curr["is_from_me"]:
                    response_times_them.append(delta / 60.0)
                elif not prev["is_from_me"] and curr["is_from_me"]:
                    response_times_me.append(delta / 60.0)

        avg_response_me = round(statistics.mean(response_times_me)) if response_times_me else None
        avg_response_them = round(statistics.mean(response_times_them)) if response_times_them else None

        avg_len_me = (
            round(statistics.mean(len((m.get("text") or "") or "") for m in from_me))
            if from_me
            else 0
        )
        avg_len_them = (
            round(statistics.mean(len((m.get("text") or "") or "") for m in from_them))
            if from_them
            else 0
        )

        return {
            "avg_response_time_me_min": avg_response_me,
            "avg_response_time_them_min": avg_response_them,
            "peak_hours_me": hours_me.most_common(3),
            "peak_hours_them": hours_them.most_common(3),
            "peak_days_me": days_me.most_common(3),
            "peak_days_them": days_them.most_common(3),
            "avg_length_me": avg_len_me,
            "avg_length_them": avg_len_them,
            "total_from_me": len(from_me),
            "total_from_them": len(from_them),
            "who_writes_more": "you" if len(from_me) > len(from_them) else "them",
            "who_writes_longer": "you" if avg_len_me > avg_len_them else "them",
        }

    def _detailed_stats(self, messages: list[dict]) -> dict:
        if not messages:
            return {}
        first = min(m["date"] for m in messages)
        last = max(m["date"] for m in messages)
        return {
            "first_message": first.isoformat(),
            "last_message": last.isoformat(),
            "total_messages": len(messages),
        }


from integrations.imessage_reader import imessage_reader

contact_analytics = ContactAnalytics(imessage_reader)
