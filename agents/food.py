"""Agent FOOD — commande de repas sur Uber Eats.

L'agent ne pilote jamais le navigateur lui-même : il rassemble le contexte
(intégration disponible ? plafonds déjà consommés ? commandes récentes ?),
formule la demande dans la voix de JARVIS, et émet un bloc ``action`` que la
couche ``actions.py`` exécute en deux passes. C'est ce découplage qui garantit
qu'aucune dépense ne peut découler d'une simple génération de texte : le
paiement dépend d'un plan serveur figé et d'une confirmation humaine.
"""

from __future__ import annotations

import logging

import config
from agents import BaseAgent

logger = logging.getLogger(__name__)

RECENT_ORDERS_SHOWN = 5


class FoodAgent(BaseAgent):
    """Agent rapide chargé des commandes de repas et de leur historique."""

    name = "food"
    description = "Commande de repas Uber Eats, historique et plafonds de dépense."
    model = config.AGENT_MODELS.get("food", config.DEEPSEEK_FAST_MODEL)

    def _enrich_context(self, context: dict | None = None) -> dict:
        """Ajoute l'état de l'intégration et l'historique récent au contexte."""
        ctx = dict(context or {})
        ctx.setdefault("user_name", config.USER_NAME)
        ctx["food_status_context"] = self._status_block()
        ctx["food_history_context"] = self._history_block()
        ctx["food_suggestions_context"] = self._suggestions_block()
        return ctx

    @staticmethod
    def _suggestions_block() -> str:
        """Résume les suggestions cliquables et la livraison en cours."""
        try:
            from database import get_active_suggestions, get_orders_awaiting_delivery

            suggestions = get_active_suggestions()
            awaiting = get_orders_awaiting_delivery(limit=1)
        except (ImportError, OSError, ValueError) as exc:
            logger.warning("[food] suggestions illisibles : %s", exc)
            return "Suggestions indisponibles."

        lines: list[str] = []
        if suggestions:
            lines.append("Suggestions cliquables depuis la page Nourriture :")
            for item in suggestions:
                articles = ", ".join(
                    f"{entry.get('quantity', 1)}x {entry.get('name')}"
                    for entry in item.get("items", [])
                )
                price = item.get("estimated_price")
                amount = f"{float(price):.2f} €" if price is not None else "prix inconnu"
                lines.append(f"- [{item['slot']}] {item['restaurant']} — {articles} — {amount}")
        else:
            lines.append("Aucune suggestion active.")

        if awaiting:
            order = awaiting[0]
            eta = order.get("eta_minutes")
            eta_text = f", arrivée estimée dans {eta} min" if eta else ""
            lines.append(
                f"Livraison en cours : {order.get('restaurant')} — "
                f"{order.get('delivery_status') or 'statut inconnu'}{eta_text}."
            )
        return "\n".join(lines)

    @staticmethod
    def _status_block() -> str:
        """Décrit en clair ce que l'intégration peut faire à cet instant."""
        try:
            from integrations.uber_eats import uber_eats

            state = uber_eats.availability()
        except (ImportError, OSError, RuntimeError) as exc:
            logger.warning("[food] état de l'intégration illisible : %s", exc)
            return "Commande Uber Eats indisponible : intégration non chargée."

        if not state["can_browse"]:
            reasons = " ; ".join(state["reasons"]) or "raison inconnue"
            return f"Commande Uber Eats indisponible. Motifs : {reasons}."
        if not state["can_place_real_order"]:
            reasons = " ; ".join(state["reasons"]) or "mode simulation actif"
            return (
                "Panier constructible mais paiement désactivé (simulation). "
                f"Motifs : {reasons}."
            )
        return (
            "Commande Uber Eats opérationnelle : panier réel, paiement possible "
            "après confirmation explicite."
        )

    @staticmethod
    def _history_block() -> str:
        """Résume les commandes récentes et la marge restante sur la journée."""
        try:
            from database import get_daily_food_order_stats, get_food_orders

            stats = get_daily_food_order_stats()
            orders = get_food_orders(limit=RECENT_ORDERS_SHOWN)
        except (ImportError, OSError, ValueError) as exc:
            logger.warning("[food] historique de commandes illisible : %s", exc)
            return "Historique de commandes indisponible."

        from integrations.uber_eats_settings import get_settings

        settings = get_settings()
        max_orders = settings.max_daily_orders
        max_spend = settings.max_daily_spend
        max_order_price = settings.max_order_price

        lines = [
            f"Aujourd'hui : {stats['orders']}/{max_orders} commande(s), "
            f"{stats['spend']:.2f} € dépensés sur un plafond de {max_spend:.2f} €.",
            f"Plafond par commande : {max_order_price:.2f} €.",
        ]
        if orders:
            lines.append("Dernières tentatives :")
            for order in orders:
                total = order.get("total_price")
                amount = f"{float(total):.2f} €" if total is not None else "total inconnu"
                lines.append(
                    f"- {order.get('created_at')} — {order.get('restaurant')} — "
                    f"{amount} — {order.get('status')}"
                )
        else:
            lines.append("Aucune commande enregistrée.")
        return "\n".join(lines)

    async def handle(
        self,
        user_message: str,
        conversation_id: int | None = None,
        context: dict | None = None,
    ) -> dict:
        """Répond en voix JARVIS et propose, si pertinent, un bloc action."""
        ctx = dict(context or {})
        if "food_status_context" not in ctx:
            ctx = self._enrich_context(ctx)
        return await self._call_llm(
            user_message,
            conversation_id=conversation_id,
            context=ctx,
            temperature=0.3,
        )


food_agent = FoodAgent()
